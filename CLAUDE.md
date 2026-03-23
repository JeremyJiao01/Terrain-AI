# CLAUDE.md

## Project Overview

**Code Graph Builder (CodeGraphWiki)** — 一个将代码仓库解析为知识图谱的工具，可生成 API 文档、向量嵌入和 Wiki 页面，并通过 MCP Server 为 AI 编程助手提供语义搜索和代码导航能力。

- **语言**: Python 3.10+
- **构建系统**: Hatchling (pyproject.toml)
- **核心依赖**: tree-sitter (AST 解析), Kuzu (图数据库), MCP (模型上下文协议)
- **许可证**: MIT

## Common Commands

```bash
# 安装（开发模式，含全部功能）
pip install -e ".[treesitter-full,semantic,rag]"

# 启动 MCP Server
cgb-mcp

# 通过 npx 启动（推荐给用户）
npx code-graph-builder --server

# 运行 CLI
code-graph-builder

# 运行测试
python3 -m pytest code_graph_builder/tests/ -v

# 单步测试（按耗时排序）
python3 -m pytest code_graph_builder/tests/test_basic.py -v                    # 快速单元测试
python3 -m pytest code_graph_builder/tests/test_step1_graph_build.py -v        # ~3 min
python3 -m pytest code_graph_builder/tests/test_step2_api_docs.py -v           # ~3 min
python3 -m pytest code_graph_builder/tests/test_step3_embedding.py -v          # ~27 min (含 API 调用)
python3 -m pytest code_graph_builder/tests/test_api_find_integration.py -v     # ~47 min (完整流水线)
```

## Architecture

### 核心流水线 (Pipeline)

```
源码 → [Tree-sitter AST 解析] → 知识图谱 (Kuzu) → API 文档 (Markdown) → 向量嵌入 → Wiki 生成
```

四个步骤:
1. **graph-build** — Tree-sitter 解析源码，构建 Kuzu 图数据库
2. **api-doc-gen** — 查询图谱，生成三级 Markdown 文档 (L1 索引 / L2 模块 / L3 函数)
3. **embed-gen** — 将函数文档向量化，存入向量存储
4. **wiki-gen** — LLM 生成多页 Wiki

### 目录结构

```
code_graph_builder/          # Python 主包
├── builder.py               # 图构建器核心
├── graph_updater.py         # 增量图更新
├── cli.py                   # CLI 入口 (code-graph-builder 命令)
├── cgb_cli.py               # CLI 辅助
├── commands_cli.py          # CLI 命令实现
├── config.py                # 配置管理
├── constants.py             # 常量定义
├── settings.py              # 设置管理
├── language_spec.py         # 语言规格定义
├── parser_loader.py         # Tree-sitter 解析器加载
├── models.py                # 数据模型
├── types.py                 # 类型定义
├── parsers/                 # AST 解析处理器
│   ├── definition_processor.py   # 定义提取
│   ├── call_processor.py         # 函数调用提取
│   ├── call_resolver.py          # 调用关系解析
│   ├── import_processor.py       # 导入语句处理
│   ├── structure_processor.py    # 结构体处理
│   ├── type_inference.py         # 类型推断
│   └── factory.py                # 解析器工厂
├── embeddings/              # 向量嵌入
│   ├── qwen3_embedder.py         # Qwen3 嵌入器（支持 DashScope/OpenAI 兼容）
│   └── vector_store.py           # 向量存储
├── mcp/                     # MCP Server
│   ├── server.py                 # MCP 服务器入口
│   ├── tools.py                  # 19 个 MCP 工具定义
│   ├── pipeline.py               # 流水线编排
│   ├── api_doc_generator.py      # API 文档生成器
│   └── file_editor.py            # 文件编辑器
├── rag/                     # RAG 模块
│   ├── rag_engine.py             # RAG 引擎
│   ├── llm_backend.py            # LLM 后端（requests + LiteLLM 兼容）
│   ├── cypher_generator.py       # 自然语言 → Cypher 查询
│   └── markdown_generator.py     # Markdown 生成
├── services/                # 服务层
│   ├── kuzu_service.py           # Kuzu 图数据库操作
│   ├── graph_service.py          # 图服务抽象
│   └── memory_service.py         # 内存服务
├── tools/                   # 工具层
│   ├── graph_query.py            # 图查询工具
│   └── semantic_search.py        # 语义搜索工具
├── guidance/                # Guidance 模块（设计文档分析）
├── utils/                   # 工具函数
├── tests/                   # 测试
└── examples/                # 示例代码

npm-package/                 # npx 分发包装
scripts/                     # 安装脚本
```

## Code Style & Conventions

- 使用 **loguru** 进行日志记录（非标准 logging）
- 异步框架: MCP server 使用 `asyncio`
- 配置管理: 通过环境变量 + `.env` 文件
- 图数据库查询: Kuzu Cypher 语法
- 代码注释和文档字符串: 中英文混合
- LLM 后端: 使用 `requests` 库，支持 LiteLLM 兼容认证
- 嵌入: 支持 DashScope (Qwen3) 和 OpenAI 兼容接口

## Key Environment Variables

| 变量 | 用途 |
|------|------|
| `LLM_API_KEY` / `OPENAI_API_KEY` | LLM API 密钥 |
| `LLM_BASE_URL` | LLM API 端点 (默认 OpenAI) |
| `LLM_MODEL` | 模型名称 (默认 gpt-4o) |
| `DASHSCOPE_API_KEY` | DashScope 嵌入 API 密钥 |
| `CGB_WORKSPACE` | 工作空间目录 (默认 ~/.code-graph-builder) |

## Supported Languages

C/C++, Python, JavaScript/TypeScript, Rust, Go, Java, Scala, C#, PHP, Lua

## Graph Schema

- **节点**: Project, Package, Module, File, Folder, Class, Function, Method, Type, Enum, Union
- **关系**: CONTAINS_*, DEFINES, DEFINES_METHOD, CALLS, INHERITS, IMPLEMENTS, IMPORTS, OVERRIDES
- **主键**: `qualified_name`
