# CodeGraphWiki (code_graph_builder) — Windows 环境使用指南

> 本文档面向 Claude Code，提供在 Windows 上从零搭建、运行本项目的完整操作步骤。

---

## 1. 项目概述

`code_graph_builder` 是一个多语言代码知识图谱构建库。它能从源码仓库中提取函数、类、调用关系等结构化信息，存储到图数据库，并基于 RAG（检索增强生成）提供代码分析能力。

**核心能力：**
- 解析 10 种编程语言（Python、JS/TS、C/C++、Rust、Go、Java、Scala、C#、PHP、Lua）
- 3 种图数据库后端（Kuzu 嵌入式、Memgraph、Memory 内存）
- 语义搜索（Qwen3 Embedding via DashScope API）
- RAG Wiki 生成（Kimi k2.5 via Moonshot API）
- MCP Server 集成

---

## 2. 环境准备（Windows）

### 2.1 前置要求

- **Python 3.11+**（建议 3.12）
- **Git**
- **C/C++ 编译工具链**（tree-sitter 编译需要）：安装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)，勾选 "C++ build tools" 工作负载

### 2.2 克隆仓库

```powershell
git clone git@github.com:JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
```

### 2.3 创建虚拟环境

```powershell
python3 -m venv .venv
.\.venv\Scripts\activate
```

> 如果 `python3` 不可用，尝试 `python -m venv .venv`。

### 2.4 安装依赖

#### 核心依赖（必装）

```powershell
pip install loguru tree-sitter kuzu requests python-dotenv diff-match-patch
```

#### Tree-sitter 语言语法包（按需安装）

```powershell
# 推荐安装常用语言
pip install tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-c tree-sitter-cpp

# 更多语言（按需）
pip install tree-sitter-rust tree-sitter-go tree-sitter-java tree-sitter-scala tree-sitter-lua
```

> **重要**：至少安装一个语言语法包，否则项目无法解析任何源码。请根据目标仓库的语言选择安装。

#### MCP Server 额外依赖

```powershell
pip install mcp
```

#### 语义搜索额外依赖（可选）

```powershell
pip install tqdm
# 如需 Qdrant 向量数据库：
pip install qdrant-client
```

#### Memgraph 后端额外依赖（可选）

```powershell
pip install mgclient
```

#### 测试依赖（可选）

```powershell
pip install pytest
```

#### 一键安装全部依赖

```powershell
pip install loguru tree-sitter kuzu requests python-dotenv diff-match-patch mcp tqdm pytest tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-c tree-sitter-cpp tree-sitter-rust tree-sitter-go tree-sitter-java tree-sitter-scala tree-sitter-lua
```

### 2.5 配置环境变量

在项目根目录复制 `.env.example` 为 `.env` 并填写 API Key：

```powershell
copy .env.example .env
```

编辑 `.env` 文件：

```ini
# 阿里云 DashScope（Qwen3 Embedding，语义搜索需要）
DASHSCOPE_API_KEY=sk-你的key

# Moonshot AI（Kimi k2.5，RAG Wiki 生成需要）
MOONSHOT_API_KEY=sk-你的key
MOONSHOT_MODEL=kimi-k2.5
```

> **注意**：如果只用图构建 + Cypher 查询，不需要任何 API Key。仅在使用语义搜索或 RAG 功能时才需要。

---

## 3. 项目结构

```
CodeGraphWiki/
├── .env.example                  # 环境变量模板
├── .env                          # 实际环境变量（不入库）
├── README.md                     # 项目说明
├── CLAUDE_CODE_GUIDE.md          # 本文档
│
└── code_graph_builder/           # 主 Python 包
    ├── __init__.py               # 包入口，导出核心 API
    ├── builder.py                # CodeGraphBuilder 主类
    ├── cli.py                    # CLI 命令行接口
    ├── config.py                 # 配置类（KuzuConfig、ScanConfig 等）
    ├── constants.py              # 枚举常量（NodeLabel、RelationshipType 等）
    ├── graph_updater.py          # 图更新逻辑
    ├── language_spec.py          # 语言规范和 tree-sitter 映射
    ├── models.py                 # 数据模型
    ├── parser_loader.py          # tree-sitter 解析器加载
    ├── types.py                  # 类型定义（BuildResult、GraphData 等）
    │
    ├── parsers/                  # 代码解析器
    │   ├── factory.py            # 解析器工厂
    │   ├── structure_processor.py
    │   ├── definition_processor.py
    │   ├── call_processor.py
    │   ├── call_resolver.py
    │   ├── import_processor.py
    │   └── type_inference.py
    │
    ├── services/                 # 数据库后端
    │   ├── kuzu_service.py       # Kuzu 嵌入式图数据库（推荐）
    │   ├── graph_service.py      # Memgraph 后端
    │   └── memory_service.py     # 内存后端（测试用）
    │
    ├── embeddings/               # 向量化
    │   ├── qwen3_embedder.py     # Qwen3 Embedding（DashScope API）
    │   └── vector_store.py       # 向量存储（Memory / Qdrant）
    │
    ├── tools/                    # 查询工具
    │   ├── graph_query.py        # 图查询服务
    │   └── semantic_search.py    # 语义搜索服务
    │
    ├── rag/                      # RAG 模块
    │   ├── rag_engine.py         # RAG 引擎
    │   ├── kimi_client.py        # Kimi k2.5 API 客户端（HTTP requests）
    │   ├── camel_agent.py        # CAMEL Agent 集成
    │   ├── markdown_generator.py # Markdown 输出
    │   ├── prompt_templates.py   # 提示词模板（移植自 deepwiki-open）
    │   └── config.py             # RAG 配置
    │
    ├── mcp/                      # MCP Server 模块
    │   ├── __init__.py           # 入口
    │   ├── server.py             # MCP stdio 服务器
    │   ├── tools.py              # MCP 工具注册表
    │   ├── pipeline.py           # 图构建 → 嵌入 → wiki 生成流水线
    │   └── file_editor.py        # AST 代码编辑工具
    │
    ├── utils/
    │   └── path_utils.py
    │
    ├── tests/                    # 单元测试
    └── examples/                 # 示例脚本
```

---

## 4. 核心使用方式

### 4.1 Python API 方式（推荐）

```python
from code_graph_builder import CodeGraphBuilder

# 构建代码知识图谱（Kuzu 嵌入式后端，无需 Docker）
builder = CodeGraphBuilder(
    repo_path="C:/Users/你的路径/target-repo",
    backend="kuzu",
    backend_config={"db_path": "./code_graph.db"},
    scan_config={
        "exclude_patterns": {"tests", "docs", "node_modules", ".git"},
        # "include_languages": {"python", "javascript"},  # 可选：仅解析指定语言
    },
)

# 构建图
result = builder.build_graph()
print(f"解析文件: {result.files_processed}")
print(f"函数数量: {result.functions_found}")
print(f"调用关系: {result.relationships_created}")

# Cypher 查询
rows = builder.query("MATCH (f:Function) RETURN f.name LIMIT 10")
for row in rows:
    print(row)

# 导出完整图数据
graph_data = builder.export_graph()
```

### 4.2 CLI 命令行方式

```powershell
# 扫描仓库并构建图
python3 -m code_graph_builder.cli scan C:\path\to\repo --backend kuzu --db-path ./graph.db

# 带排除规则扫描
python3 -m code_graph_builder.cli scan C:\path\to\repo --exclude tests,docs --db-path ./graph.db

# 查询图
python3 -m code_graph_builder.cli query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db

# 导出为 JSON
python3 -m code_graph_builder.cli export C:\path\to\repo --output ./graph.json --build

# 查看图统计信息
python3 -m code_graph_builder.cli stats --db-path ./graph.db
```

### 4.3 内存模式（无持久化，适合快速测试）

```python
from code_graph_builder import CodeGraphBuilder

builder = CodeGraphBuilder(
    repo_path="C:/path/to/repo",
    backend="memory",
)
result = builder.build_graph()
data = builder.export_graph()  # 返回完整图数据字典
```

---

## 5. 高级功能

### 5.1 语义搜索

**前置条件**：设置 `DASHSCOPE_API_KEY` 环境变量。

```python
from code_graph_builder import CodeGraphBuilder, create_embedder, create_vector_store
from code_graph_builder.tools.semantic_search import SemanticSearchService

builder = CodeGraphBuilder("C:/path/to/repo", backend="kuzu",
                           backend_config={"db_path": "./graph.db"})
builder.build_graph()

embedder = create_embedder(provider="qwen3")
vector_store = create_vector_store(backend="memory", dimension=1536)

service = SemanticSearchService(embedder=embedder, vector_store=vector_store)
results = service.search("recursive fibonacci implementation", top_k=5)
```

### 5.2 RAG Wiki 生成

**前置条件**：设置 `MOONSHOT_API_KEY` 环境变量。

```powershell
# 使用示例脚本（以 tinycc 为例）
python3 code_graph_builder/examples/test_rag_tinycc.py --repo-path C:\path\to\tinycc --max-pages 10 --output-dir ./rag_output

# 使用 Redis 示例
python3 code_graph_builder/examples/test_rag_redis.py --repo-path C:\path\to\redis --max-pages 10
```

### 5.3 MCP Server

MCP Server 通过 stdio 传输暴露图查询、语义搜索和代码检索工具。

```powershell
# 启动 MCP Server
set CGB_WORKSPACE=%USERPROFILE%\.code-graph-builder
python3 -m code_graph_builder.mcp.server
```

**MCP 工作区布局**（`CGB_WORKSPACE` 目录下）：

```
~/.code-graph-builder/
├── active.txt                    # 当前活跃的 artifact 目录名
└── {repo_name}_{hash8}/
    ├── meta.json                 # 元数据
    ├── graph.db                  # Kuzu 图数据库
    ├── vectors.pkl               # 嵌入缓存
    └── wiki/                     # 生成的 wiki 页面
```

**在 Claude Code 中配置 MCP Server**：在 `.claude/settings.json` 或项目的 `.mcp.json` 中添加：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "python3",
      "args": ["-m", "code_graph_builder.mcp.server"],
      "cwd": "C:\\path\\to\\CodeGraphWiki",
      "env": {
        "CGB_WORKSPACE": "C:\\Users\\你的用户名\\.code-graph-builder",
        "MOONSHOT_API_KEY": "sk-你的key",
        "DASHSCOPE_API_KEY": "sk-你的key"
      }
    }
  }
}
```

---

## 6. 图模式（Graph Schema）

### 节点类型

| 标签 | 说明 | 关键属性 |
|------|------|----------|
| `Project` | 项目根节点 | `name` |
| `Package` | 包 | `name`, `path` |
| `Folder` | 目录 | `name`, `path` |
| `File` | 源文件 | `name`, `path` |
| `Module` | 模块 | `name`, `qualified_name` |
| `Class` | 类 | `name`, `qualified_name`, `start_line`, `end_line` |
| `Function` | 函数 | `name`, `qualified_name`, `start_line`, `end_line` |
| `Method` | 方法 | `name`, `qualified_name`, `start_line`, `end_line` |

### 关系类型

| 类型 | 说明 |
|------|------|
| `CONTAINS_FILE` / `CONTAINS_FOLDER` / `CONTAINS_PACKAGE` | 包含关系 |
| `DEFINES` / `DEFINES_METHOD` | 定义关系 |
| `CALLS` | 函数调用 |
| `INHERITS` | 类继承 |
| `IMPLEMENTS` | 接口实现 |
| `IMPORTS` | 导入关系 |
| `EXPORTS` | 导出关系 |

### 常用 Cypher 查询示例

```cypher
-- 查询所有函数
MATCH (f:Function) RETURN f.name, f.qualified_name LIMIT 20

-- 查询函数调用关系
MATCH (caller:Function)-[:CALLS]->(callee:Function)
RETURN caller.name, callee.name LIMIT 20

-- 查询某个函数的所有调用者
MATCH (caller:Function)-[:CALLS]->(f:Function {name: 'target_function'})
RETURN caller.name, caller.qualified_name

-- 查询类的继承关系
MATCH (child:Class)-[:INHERITS]->(parent:Class)
RETURN child.name, parent.name

-- 查询某文件定义的所有函数
MATCH (file:File {path: 'src/main.py'})-[:DEFINES]->(f:Function)
RETURN f.name, f.start_line, f.end_line

-- 统计各类节点数量
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC
```

---

## 7. 环境变量参考

| 变量名 | 用途 | 必需场景 | 默认值 |
|--------|------|----------|--------|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（Qwen3 Embedding） | 语义搜索 | 无 |
| `MOONSHOT_API_KEY` | Moonshot AI（Kimi k2.5） | RAG Wiki 生成 | 无 |
| `MOONSHOT_MODEL` | Kimi 模型名称 | RAG Wiki 生成 | `kimi-k2.5` |
| `MEMGRAPH_HOST` | Memgraph 主机 | Memgraph 后端 | `localhost` |
| `MEMGRAPH_PORT` | Memgraph 端口 | Memgraph 后端 | `7687` |
| `CGB_WORKSPACE` | MCP 工作区路径 | MCP Server | `~/.code-graph-builder/` |

---

## 8. Windows 特别注意事项

1. **路径分隔符**：Python 代码中使用正斜杠 `/` 或原始字符串 `r"C:\path\to\repo"`，避免转义问题。
2. **编码问题**：项目统一使用 UTF-8。如果终端输出乱码，执行 `chcp 65001` 切换到 UTF-8 代码页。
3. **tree-sitter 编译**：首次安装 tree-sitter 语言语法包时需要 C 编译器。确保已安装 Visual Studio Build Tools。
4. **长路径限制**：如遇路径过长错误，在注册表中启用 Windows 长路径支持：
   ```powershell
   # 以管理员身份运行
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
   ```
5. **虚拟环境激活**：每次打开新终端需要重新激活：
   ```powershell
   .\.venv\Scripts\activate
   ```

---

## 9. 运行测试

```powershell
# 单元测试
python3 -m pytest code_graph_builder/tests/ -v

# RAG 模块测试
python3 -m pytest code_graph_builder/rag/tests/ -v

# 快速验证导入
python3 -c "from code_graph_builder import CodeGraphBuilder; print('OK')"
```

---

## 10. 常见问题排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `No language parsers could be loaded` | 未安装 tree-sitter 语言语法包 | `pip install tree-sitter-python tree-sitter-c` 等 |
| `ModuleNotFoundError: No module named 'loguru'` | 未安装核心依赖 | `pip install loguru` |
| `ImportError: No module named 'kuzu'` | 未安装 Kuzu | `pip install kuzu` |
| `MOONSHOT_API_KEY 未设置` | 环境变量未配置 | 创建 `.env` 文件或 `set MOONSHOT_API_KEY=sk-xxx` |
| `error: Microsoft Visual C++ 14.0 or greater is required` | 缺少 C++ 编译器 | 安装 Visual Studio Build Tools |
| Kuzu 数据库锁定错误 | 上次进程未正常退出 | 删除 `.db` 目录重新构建 |
