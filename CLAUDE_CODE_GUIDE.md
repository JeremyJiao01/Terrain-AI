# CodeGraphWiki (code_graph_builder) — 使用指南

> 本文档面向 AI Agent（如 Claude Code），提供从零搭建、运行本项目的完整操作步骤，包括交互式配置、连接测试和 MCP 自动安装。

---

## 0. Agent 交互式配置流程（首次安装必读）

> **本节面向调用本项目的 AI Agent**：当用户首次配置 CodeGraphWiki MCP 时，请按照以下流程交互式引导用户完成配置。

### 0.1 第一步：询问用户选择 LLM 平台

请依次询问用户以下信息：

**LLM 配置（用于 Wiki 生成、Cypher 查询翻译等功能）：**

1. **选择 LLM 平台**：询问用户使用哪个 LLM 服务提供商：
   - OpenAI（api.openai.com）
   - DeepSeek（api.deepseek.com）
   - Moonshot / Kimi（api.moonshot.cn）
   - 其他 OpenAI 兼容平台（用户自定义 URL）
   - 暂不配置（仅使用图构建和 Cypher 查询，不需要 LLM）

2. **LLM API Base URL**：根据用户选择自动填充，或由用户提供自定义 URL。常见默认值：
   - OpenAI: `https://api.openai.com/v1`
   - DeepSeek: `https://api.deepseek.com/v1`
   - Moonshot: `https://api.moonshot.cn/v1`

3. **LLM API Key**：请用户提供对应平台的 API Key（如 `sk-xxxxxx`）。

4. **LLM 模型名称**（可选）：用户可指定模型名。常见默认值：
   - OpenAI: `gpt-4o`
   - DeepSeek: `deepseek-chat`
   - Moonshot: `kimi-k2.5`

### 0.2 第二步：询问用户配置 Embedding 模型

**Embedding 配置（用于语义搜索功能）：**

1. **Embedding API Key**：默认使用阿里云 DashScope（Qwen3 text-embedding-v4）。请用户提供 `DASHSCOPE_API_KEY`（如 `sk-xxxxxx`），可在阿里云百炼平台获取。

2. **Embedding API Base URL**（可选）：默认 `https://dashscope.aliyuncs.com/api/v1`，一般无需修改。如用户使用自建或代理服务，可提供自定义 URL。

3. 如果用户暂不配置 Embedding，语义搜索功能将不可用，但图构建和 Cypher 查询功能不受影响。

### 0.3 第三步：创建并运行连接测试脚本

在用户提供完所有配置信息后，**Agent 应主动创建连接测试脚本**来验证配置是否正确。

#### LLM 连接测试脚本示例

在项目目录下创建临时测试脚本 `_test_llm_connection.py`：

```python
"""LLM 连接测试脚本 — 验证 LLM API 配置是否正确"""
import httpx
import sys

# ===== 用户配置（Agent 根据用户输入填写） =====
LLM_API_KEY = "sk-用户提供的key"
LLM_BASE_URL = "https://api.openai.com/v1"  # 根据用户选择的平台填写
LLM_MODEL = "gpt-4o"  # 根据用户选择的平台填写
# ============================================

def test_llm():
    print(f"测试 LLM 连接...")
    print(f"  Base URL: {LLM_BASE_URL}")
    print(f"  Model:    {LLM_MODEL}")
    print(f"  API Key:  {LLM_API_KEY[:8]}...{LLM_API_KEY[-4:]}")

    try:
        resp = httpx.post(
            f"{LLM_BASE_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 10,
            },
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            print(f"  ✅ LLM 连接成功！回复: {reply}")
            return True
        else:
            print(f"  ❌ LLM 连接失败！HTTP {resp.status_code}")
            print(f"  响应: {resp.text[:500]}")
            return False
    except Exception as e:
        print(f"  ❌ LLM 连接异常: {e}")
        return False

if __name__ == "__main__":
    ok = test_llm()
    sys.exit(0 if ok else 1)
```

#### Embedding 连接测试脚本示例

在项目目录下创建临时测试脚本 `_test_embedding_connection.py`：

```python
"""Embedding 连接测试脚本 — 验证 Embedding API 配置是否正确"""
import requests
import sys

# ===== 用户配置（Agent 根据用户输入填写） =====
DASHSCOPE_API_KEY = "sk-用户提供的key"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"  # 通常无需修改
EMBEDDING_MODEL = "text-embedding-v4"  # 通常无需修改
# ============================================

def test_embedding():
    print(f"测试 Embedding 连接...")
    print(f"  Base URL: {DASHSCOPE_BASE_URL}")
    print(f"  Model:    {EMBEDDING_MODEL}")
    print(f"  API Key:  {DASHSCOPE_API_KEY[:8]}...{DASHSCOPE_API_KEY[-4:]}")

    url = f"{DASHSCOPE_BASE_URL}/services/embeddings/text-embedding/text-embedding"
    try:
        resp = requests.post(
            url,
            json={
                "model": EMBEDDING_MODEL,
                "input": {"texts": ["hello world"]},
                "parameters": {"text_type": "document"},
            },
            headers={
                "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            dim = len(data["output"]["embeddings"][0]["embedding"])
            print(f"  ✅ Embedding 连接成功！向量维度: {dim}")
            return True
        else:
            print(f"  ❌ Embedding 连接失败！HTTP {resp.status_code}")
            print(f"  响应: {resp.text[:500]}")
            return False
    except Exception as e:
        print(f"  ❌ Embedding 连接异常: {e}")
        return False

if __name__ == "__main__":
    ok = test_embedding()
    sys.exit(0 if ok else 1)
```

#### 测试流程

1. Agent 根据用户提供的信息，填入脚本中的配置变量
2. 运行 `python _test_llm_connection.py` 和 `python _test_embedding_connection.py`
3. 如果测试通过（✅），继续下一步配置 MCP
4. 如果测试失败（❌），根据错误信息排查：

| 错误信息 | 可能原因 | 修复方法 |
|----------|----------|----------|
| `HTTP 401` / `Unauthorized` | API Key 无效或过期 | 请用户重新提供正确的 API Key |
| `HTTP 403` / `Forbidden` | API Key 权限不足或账号欠费 | 请用户检查账户余额和 Key 权限 |
| `HTTP 404` / `Not Found` | Base URL 错误或模型名错误 | 检查 URL 和模型名是否正确 |
| `HTTP 429` / `Too Many Requests` | 请求频率超限 | 等待后重试，或请用户升级套餐 |
| `Connection refused` / `timeout` | 网络不通或 URL 拼写错误 | 检查网络连接和 URL |
| `SSL error` | 证书问题或代理配置 | 检查代理设置或添加 `verify=False`（仅调试用） |
| `Invalid model` | 模型名拼写错误 | 请用户确认模型名称 |

5. Agent 根据错误信息自动调整配置并重新测试，直到全部通过
6. 测试通过后删除临时测试脚本

### 0.4 第四步：自动配置 Claude Code MCP

连接测试全部通过后，Agent 应主动为用户配置 MCP Server。

#### 确定项目路径

首先确认 CodeGraphWiki 项目的实际安装路径（即包含 `code_graph_builder/` 目录的路径）。

#### 生成 MCP 配置

根据操作系统和用户提供的配置，生成 MCP 配置 JSON：

**Windows 配置**（`%APPDATA%\Claude\claude_desktop_config.json` 或 `~/.claude/settings.json`）：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "python3",
      "args": ["-m", "code_graph_builder.mcp.server"],
      "cwd": "C:\\path\\to\\CodeGraphWiki",
      "env": {
        "CGB_WORKSPACE": "C:\\Users\\用户名\\.code-graph-builder",
        "LLM_API_KEY": "sk-用户的LLM-Key",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL": "gpt-4o",
        "DASHSCOPE_API_KEY": "sk-用户的Embedding-Key",
        "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/api/v1"
      }
    }
  }
}
```

**macOS / Linux 配置**（`~/.claude/settings.json`）：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "python3",
      "args": ["-m", "code_graph_builder.mcp.server"],
      "cwd": "/path/to/CodeGraphWiki",
      "env": {
        "CGB_WORKSPACE": "~/.code-graph-builder",
        "LLM_API_KEY": "sk-用户的LLM-Key",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL": "gpt-4o",
        "DASHSCOPE_API_KEY": "sk-用户的Embedding-Key",
        "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/api/v1"
      }
    }
  }
}
```

#### Claude Code MCP 安装步骤

Agent 应按照以下步骤为用户自动完成 MCP 配置：

1. **检测 Claude Code 配置文件位置**：
   - 检查项目根目录是否存在 `.mcp.json`（项目级配置，优先）
   - 检查用户主目录下 `~/.claude/settings.json`（全局配置）
   - Windows: 也可查看 `%APPDATA%\Claude\claude_desktop_config.json`

2. **读取现有配置**：如果配置文件已存在，读取并保留其他 MCP Server 配置，仅添加或更新 `code-graph-builder` 条目。

3. **写入配置**：将上一步生成的 MCP 配置合并写入配置文件。

4. **验证配置是否生效**：
   - 提示用户重启 Claude Code（或运行 `/mcp` 命令查看 MCP Server 状态）
   - 或直接尝试调用 `initialize_repository` 工具来验证

#### 环境变量说明

Agent 在配置 MCP 时需要设置以下环境变量（根据用户在 0.1 和 0.2 步骤中提供的信息）：

| 环境变量 | 用途 | 示例值 |
|----------|------|--------|
| `CGB_WORKSPACE` | MCP 工作区路径 | `~/.code-graph-builder` |
| `LLM_API_KEY` | LLM API 密钥（通用，最高优先级） | `sk-xxxxxx` |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.openai.com/v1` |
| `LLM_MODEL` | LLM 模型名 | `gpt-4o` |
| `DASHSCOPE_API_KEY` | Embedding API 密钥 | `sk-xxxxxx` |
| `DASHSCOPE_BASE_URL` | Embedding API 地址（可选） | `https://dashscope.aliyuncs.com/api/v1` |

> **优先级说明**：LLM 密钥检测优先级为 `LLM_API_KEY` > `OPENAI_API_KEY` > `MOONSHOT_API_KEY`，推荐统一使用 `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` 组合。

### 0.5 完整交互流程总结

```
Agent 引导用户完成 MCP 首次配置：

1. 询问 → 用户选择 LLM 平台、提供 API Key 和 URL
2. 询问 → 用户提供 Embedding (DashScope) API Key
3. 创建 → 生成连接测试脚本
4. 测试 → 运行测试脚本验证连接
   ├── 通过 → 继续下一步
   └── 失败 → 根据错误信息修改配置，重新测试
5. 配置 → 自动写入 Claude Code MCP 配置文件
6. 验证 → 提示用户重启或验证 MCP 连接
7. 清理 → 删除临时测试脚本
```

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
# LLM 配置（用于 Wiki 生成、Cypher 查询翻译等，三组中任选一组）
# 推荐使用 LLM_API_KEY（通用配置，支持任意 OpenAI 兼容平台）
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o

# 阿里云 DashScope（Qwen3 Embedding，语义搜索需要）
DASHSCOPE_API_KEY=sk-你的key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
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

**在 Claude Code 中配置 MCP Server**：在 `~/.claude/settings.json` 或项目的 `.mcp.json` 中添加：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "python3",
      "args": ["-m", "code_graph_builder.mcp.server"],
      "cwd": "C:\\path\\to\\CodeGraphWiki",
      "env": {
        "CGB_WORKSPACE": "C:\\Users\\你的用户名\\.code-graph-builder",
        "LLM_API_KEY": "sk-你的LLM-Key",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL": "gpt-4o",
        "DASHSCOPE_API_KEY": "sk-你的Embedding-Key",
        "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/api/v1"
      }
    }
  }
}
```

> **首次配置？** 建议使用本文档第 0 节的交互式配置流程，由 Agent 自动完成连接测试和 MCP 配置。

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

### LLM 配置（优先级从高到低，首个匹配生效）

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `LLM_API_KEY` | 通用 LLM API 密钥（最高优先级） | 无 |
| `LLM_BASE_URL` | 通用 LLM API 地址 | `https://api.openai.com/v1` |
| `LLM_MODEL` | 通用 LLM 模型名 | `gpt-4o` |
| `OPENAI_API_KEY` | OpenAI / 兼容平台 API 密钥 | 无 |
| `OPENAI_BASE_URL` | OpenAI / 兼容平台 API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | OpenAI 模型名 | `gpt-4o` |
| `MOONSHOT_API_KEY` | Moonshot AI（Kimi）API 密钥（旧版兼容） | 无 |
| `MOONSHOT_MODEL` | Kimi 模型名称 | `kimi-k2.5` |

### Embedding 配置

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（Qwen3 Embedding） | 无 |
| `DASHSCOPE_BASE_URL` | DashScope API 地址 | `https://dashscope.aliyuncs.com/api/v1` |

### 其他配置

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `CGB_WORKSPACE` | MCP 工作区路径 | `~/.code-graph-builder/` |
| `MEMGRAPH_HOST` | Memgraph 主机 | `localhost` |
| `MEMGRAPH_PORT` | Memgraph 端口 | `7687` |

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
