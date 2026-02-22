# code_graph_builder

多语言代码知识图谱构建库，支持从源码仓库提取函数、类、调用关系等，存储到图数据库，并提供 RAG（检索增强生成）能力用于代码分析。

## 特性

- **多语言解析**：Python、JavaScript/TypeScript、C/C++、Rust、Go、Java、Scala、C#、PHP、Lua
- **多后端支持**：Kùzu（嵌入式，无需 Docker）、Memgraph（完整图数据库）、Memory（内存，测试用）
- **语义搜索**：基于 Qwen3 Embedding（阿里云 DashScope API）的向量化代码检索
- **RAG 引擎**：结合图数据和向量搜索，用 Kimi k2.5 生成代码分析报告

## 目录结构

```
code_graph_builder/
├── builder.py              # 主 API：CodeGraphBuilder
├── cli.py                  # 命令行接口
├── config.py               # 配置类（KuzuConfig、ScanConfig 等）
├── constants.py            # 常量和 StrEnum
├── graph_updater.py        # 图更新和批量写入
├── language_spec.py        # 语言规范和 Tree-sitter 映射
├── models.py               # 数据类
├── parser_loader.py        # Tree-sitter 解析器加载
├── types.py                # 类型定义（BuildResult、GraphData 等）
│
├── parsers/                # 各类解析器
│   ├── factory.py          # 解析器工厂
│   ├── structure_processor.py
│   ├── definition_processor.py
│   ├── call_processor.py
│   ├── call_resolver.py
│   ├── import_processor.py
│   └── type_inference.py
│
├── services/               # 后端适配层
│   ├── graph_service.py    # Memgraph 后端
│   ├── kuzu_service.py     # Kùzu 后端
│   └── memory_service.py   # 内存后端
│
├── embeddings/             # 向量化
│   ├── qwen3_embedder.py   # Qwen3 嵌入器（阿里云 API）
│   └── vector_store.py     # 向量存储抽象
│
├── tools/                  # 查询工具
│   ├── graph_query.py      # 图查询
│   └── semantic_search.py  # 语义搜索
│
├── rag/                    # RAG 模块
│   ├── rag_engine.py       # RAG 引擎
│   ├── kimi_client.py      # Kimi API 客户端
│   ├── camel_agent.py      # CAMEL Agent
│   ├── markdown_generator.py
│   ├── prompt_templates.py # 提示词模板（移植自 deepwiki）
│   └── config.py
│
├── utils/
│   └── path_utils.py
│
├── tests/                  # 单元测试
└── examples/               # 示例和演示脚本
```

## 快速开始

### 安装

```bash
# 基础依赖（含 Kùzu 后端）
uv sync

# 含语义搜索（Qwen3 嵌入）
uv sync --extra semantic

# 含所有 Tree-sitter 语言语法
uv sync --extra treesitter-full
```

### 基本用法

```python
from code_graph_builder import CodeGraphBuilder

# 构建代码图（Kùzu 后端，无需 Docker）
builder = CodeGraphBuilder(
    repo_path="/path/to/your/repo",
    backend="kuzu",
    backend_config={"db_path": "./code_graph.db"},
)
result = builder.build_graph()

print(f"解析文件: {result.files_processed}")
print(f"函数数量: {result.functions_found}")
print(f"调用关系: {result.relationships_created}")

# 导出图数据
graph_data = builder.export_graph()

# 执行 Cypher 查询
rows = builder.query("MATCH (f:Function) RETURN f.name LIMIT 10")
```

### 内存模式（无持久化，适合测试）

```python
builder = CodeGraphBuilder(
    repo_path="/path/to/repo",
    backend="memory",
)
result = builder.build_graph()
data = builder.export_graph()  # 返回完整图数据
```

### 语义搜索

需要配置阿里云 DashScope API Key：

```bash
export DASHSCOPE_API_KEY=sk-xxxxxx
```

```python
from code_graph_builder import CodeGraphBuilder, create_embedder, create_vector_store
from code_graph_builder.tools.semantic_search import SemanticSearchService

builder = CodeGraphBuilder("/path/to/repo", backend="kuzu")
builder.build_graph()

embedder = create_embedder(provider="qwen3")
vector_store = create_vector_store(backend="memory", dimension=1536)

# 构建向量索引（首次需要调用 API）
service = SemanticSearchService(
    embedder=embedder,
    vector_store=vector_store,
)
results = service.search("recursive fibonacci implementation", top_k=5)
```

### RAG Wiki 生成

需要配置 Moonshot API Key（Kimi k2.5）：

```bash
export MOONSHOT_API_KEY=sk-xxxxxx
```

```python
# 参考 examples/test_rag_tinycc.py
# 按模块批量生成代码 wiki，真实源码作为上下文

python code_graph_builder/examples/test_rag_tinycc.py \
    --repo-path /path/to/tinycc \
    --max-pages 10 \
    --output-dir ./rag_output
```

## 环境变量

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（Qwen3 嵌入） | 无 |
| `MOONSHOT_API_KEY` | Moonshot AI（Kimi k2.5 RAG） | 无 |
| `MOONSHOT_MODEL` | Kimi 模型名 | `kimi-k2.5` |
| `MEMGRAPH_HOST` | Memgraph 主机（仅 Memgraph 后端） | `localhost` |
| `MEMGRAPH_PORT` | Memgraph 端口 | `7687` |

## 支持的编程语言

| 语言 | 提取内容 |
|------|----------|
| Python | 函数、类、方法、导入、调用关系 |
| JavaScript / TypeScript | 函数、类、模块、调用关系 |
| C / C++ | 函数、结构体、调用关系 |
| Rust | 函数、impl 块、trait、调用关系 |
| Go | 函数、接口、调用关系 |
| Java | 类、方法、继承、调用关系 |
| Scala | 类、对象、方法 |
| C# | 类、方法、命名空间 |
| PHP | 函数、类、方法 |
| Lua | 函数、调用关系 |

## 图模式（Graph Schema）

**节点类型**：`Project`、`Package`、`Module`、`File`、`Class`、`Function`、`Method`、`Folder`

**关系类型**：`CONTAINS`、`DEFINES`、`CALLS`、`INHERITS`、`IMPORTS`

## 示例脚本

位于 `examples/` 目录：

| 脚本 | 用途 |
|------|------|
| `example_kuzu_usage.py` | Kùzu 后端完整示例 |
| `example_configuration.py` | 各种配置方式演示 |
| `test_tinycc.py` | 用 tinycc 仓库测试图构建 |
| `test_kuzu_local.py` | Kùzu 后端功能测试 |
| `test_tinycc_memory.py` | 内存模式解析测试 |
| `test_rag_tinycc.py` | RAG wiki 生成（含真实源码上下文） |
| `rag_example.py` | RAG 模块使用示例 |
| `example_semantic_search_full.py` | 完整语义搜索流程 |
| `test_embedding_api.py` | Qwen3 嵌入 API 测试 |

## RAG 提示词模板

`rag/prompt_templates.py` 移植自 [deepwiki-open](https://github.com/AsyncFuncAI/deepwiki-open)，包含：

- `RAG_SYSTEM_PROMPT` / `RAG_TEMPLATE` — 标准 RAG 提示词
- `DEEP_RESEARCH_*_ITERATION_PROMPT` — 多轮深度研究提示词
- `SIMPLE_CHAT_SYSTEM_PROMPT` — 直接问答提示词

源码上下文注入方案：通过 `qualified_name` 推导源文件路径，用 `start_line`/`end_line` 精确提取函数体，按 `## File Path: xxx.c` 格式组装进 prompt。

## 运行测试

```bash
# 单元测试（无需外部依赖）
uv run pytest code_graph_builder/tests/ -v

# RAG 模块测试
uv run pytest code_graph_builder/rag/tests/ -v
```
