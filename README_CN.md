# Terrain

[English](README.md) | 中文

[![CI](https://github.com/JeremyJiao01/Terrain-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/JeremyJiao01/Terrain-AI/actions)
[![PyPI](https://img.shields.io/pypi/v/terrain-ai)](https://pypi.org/project/terrain-ai/)
[![npm](https://img.shields.io/npm/v/terrain-ai)](https://www.npmjs.com/package/terrain-ai)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org)

让你的 AI 编程助手真正读懂任意代码库 —— 函数签名、调用图谱、全库语义搜索，一次索引，随时查询。

## 问题

把一个 50 万行的代码库丢给 Claude Code。它能读到的，它读；读不到的，它猜。你得到的答案——差不多对。

Terrain 把整个代码库索引一次，给你的 AI 一张精确、可查询的知识图谱。它不再猜了。

## 魔法时刻

对着一个陌生代码库问 Claude Code：

> "认证 token 是怎么刷新的？"

没有 Terrain：AI 扫几个文件，猜一个答案，可能错过三层调用之外的真实实现。

有了 Terrain：

```
find_api("认证 token 刷新")

→ refresh_access_token()  auth/token_manager.c:187
  签名：int refresh_access_token(TokenCtx *ctx, const char *refresh_token)
  调用路径：session_heartbeat() → event_loop_tick() → main()
  内部调用：http_post()、parse_jwt()、update_session_store()
```

精确。完整。即时。

## 快速开始

```bash
npx terrain-ai@latest --setup
```

配置向导自动安装 Python 包、配置 LLM 和 Embedding 提供商，并将 Terrain 注册为 Claude Code 的全局 MCP 服务器。一条命令搞定。

然后在 Claude Code 里——指向任意代码库，问任何问题。

## 索引代码库

```bash
terrain index /path/to/your/repo
```

首次需要几分钟。之后增量更新：

```bash
terrain index -i   # 基于 git-diff，很快
```

## 你可以问什么

| 你想知道… | 怎么问 |
|---|---|
| X 在哪里初始化？ | "找 X 的初始化" |
| 谁调用了这个函数？ | "找 function\_name 的调用者" |
| 功能 Y 的完整执行路径？ | "追踪 Y 的调用链" |
| 哪些函数处理 Z？ | "找 Z 的处理函数" |

## 支持的语言

C/C++、Python、JavaScript/TypeScript、Rust、Go、Java、Scala、C#、PHP、Lua

---

## 参考文档

### 安装

```bash
# 推荐方式
npx terrain-ai@latest --setup

# 或通过 pip
pip install terrain-ai
terrain-mcp  # 启动 MCP 服务器
```

### 卸载

```bash
npx terrain-ai@latest --uninstall
```

移除：Claude MCP 注册、Python 包、工作区数据。

### MCP 客户端配置

添加到你的 MCP 客户端配置（Claude Code、Cursor、Windsurf 等）：

```json
{
  "mcpServers": {
    "terrain": {
      "command": "npx",
      "args": ["-y", "terrain-ai@latest", "--server"]
    }
  }
}
```

Windows 平台：

```json
{
  "mcpServers": {
    "terrain": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "terrain-ai@latest", "--server"]
    }
  }
}
```

### 命令行工具 (`terrain`)

#### 工作区

```bash
terrain status              # 显示活跃仓库、工作区、LLM 和 Embedding 信息
terrain list                # 列出所有已索引仓库
terrain repo                # 交互式切换活跃仓库
terrain config              # 交互式配置向导（LLM、Embedding、工作区）
terrain link <path>         # 将本地仓库关联到共享的预构建产物
terrain link <path> --db x  # 关联到指定产物目录
```

#### 索引

```bash
terrain index               # 索引当前目录（图谱 → API 文档 → 嵌入向量）
terrain index /path/to/repo # 索引指定路径
terrain index -i            # 增量更新（基于 git-diff，速度快）
terrain index --no-embed    # 跳过嵌入向量生成
terrain index --no-wiki     # 仅跳过 wiki 生成
```

#### 重建与清理

```bash
terrain rebuild             # 重建活跃仓库的所有步骤
terrain rebuild --step graph   # 仅重建图谱
terrain rebuild --step api     # 仅重建 API 文档
terrain rebuild --step embed   # 仅重建嵌入向量
terrain rebuild --step wiki    # 仅重建 wiki

terrain clean               # 删除索引数据（交互式选择）
terrain clean repo_name     # 删除指定仓库
terrain clean --all         # 删除所有已索引仓库
```

#### 底层命令

```bash
terrain scan /path          # 扫描仓库并构建知识图谱
  --backend kuzu|memgraph|memory
  --db-path ./graph.db
  --exclude "vendor,build"
  --language "c,python"
  --clean               # 扫描前清空数据库
  -o graph.json         # 导出图谱为 JSON

terrain query "MATCH (f:Function) RETURN f.name LIMIT 10"
  --format table|json

terrain export /path -o graph.json
  --build               # 导出前先构建图谱

terrain stats               # 显示图谱统计信息（节点数、关系数）
```

#### 全局参数

```bash
terrain --version           # 显示版本
terrain -v ...              # 详细/调试输出
terrain --help              # 显示帮助
```

### MCP 工具

**AI Agent 核心工作流：** `initialize_repository` → `find_api` → `get_api_doc`

#### 仓库管理

| 工具 | 说明 |
|---|---|
| `initialize_repository` | 索引仓库：图谱 + API 文档 + 嵌入向量 |
| `get_repository_info` | 当前仓库统计（节点/关系数量、服务状态） |
| `list_repositories` | 所有已索引仓库及流水线完成状态 |
| `switch_repository` | 切换当前查询的仓库 |
| `link_repository` | 复用已有索引到其他仓库路径（无需重新索引） |

#### 代码搜索与文档

| 工具 | 说明 |
|---|---|
| `find_api` | 混合语义 + 关键词搜索，附带 API 文档（主要搜索工具） |
| `list_api_docs` | 浏览 L1 模块索引或 L2 模块详情 |
| `get_api_doc` | L3 函数详情：签名、调用树、使用示例、源码 |
| `generate_api_docs` | 生成/更新 API 文档（full / resume / enhance） |

#### 调用图分析

| 工具 | 说明 |
|---|---|
| `find_callers` | 查找所有调用指定函数的函数（无需 LLM） |
| `trace_call_chain` | BFS 向上调用链追踪，发现入口点 |

#### 配置与维护

| 工具 | 说明 |
|---|---|
| `get_config` | 显示服务器配置和服务可用性 |
| `rebuild_embeddings` | 构建或重建向量嵌入 |

### 流水线

| 步骤 | 内容 | 输入 | 输出 |
|---|---|---|---|
| 1. graph-build | Tree-sitter AST 解析 | 源代码 | Kuzu 图数据库 |
| 2. api-doc-gen | 查询图谱，渲染文档 | 图谱 | 三级 Markdown（索引 / 模块 / 函数） |
| 2b. desc-gen | LLM 生成描述 | 缺少文档字符串的函数 | L3 Markdown 中的描述 |
| 3. embed-gen | 函数文档向量化 | L3 Markdown 文件 | 向量存储 (pickle) |

```
initialize_repository  →  步骤 1-3（完整流水线）
build_graph            →  仅步骤 1
generate_api_docs      →  步骤 2 + 2b（模式：full / resume / enhance）
rebuild_embeddings     →  步骤 3
generate_wiki          →  独立功能（不在主流水线中）
```

### API 文档格式

生成的文档同时针对 AI Agent 阅读和向量检索进行了优化。

#### L3 函数详情（嵌入单元）

```markdown
# parse_btype

> 解析基本类型声明，包括 struct/union/enum 说明符。

- 签名：`int parse_btype(CType *type, AttributeDef *ad, int ignore_label)`
- 返回值：`int`
- 可见性：static | 头文件：tccgen.h
- 位置：tccgen.c:139-280
- 模块：tinycc.tccgen — C 代码生成器

## 调用树

parse_btype
├── expr_const           [static]
├── parse_btype_qualify   [static]
├── struct_decl           [static]
│   ├── expect
│   └── next
└── parse_attribute       [static]

## 被调用方 (5)

- type_decl (tinycc.tccgen) → tccgen.c:1200
- post_type (tinycc.tccgen) → tccgen.c:1350
```

#### C/C++ 特有功能

- 提取函数上方的 `//` 和 `/* */` 注释作为描述
- 显示 struct/union/enum 成员及类型
- 宏定义独立章节
- static/public/extern 可见性分类
- 从签名推断内存所有权
- 头文件/实现文件分离
- 通过 `#include` 头文件映射实现跨文件函数调用解析
- 函数指针追踪与间接调用解析
- 支持 GB2312/GBK 编码的源文件

### 支持的语言（详细）

| 语言 | 函数 | 类/结构体 | 调用 | 导入 | 类型 |
|---|---|---|---|---|---|
| C / C++ | 是 | struct, union, enum, typedef, macro | 是 | #include | 是 |
| Python | 是 | 是 | 是 | 是 | - |
| JavaScript / TypeScript | 是 | 是 | 是 | 是 | - |
| Rust | 是 | struct, enum, trait, impl | 是 | 是 | - |
| Go | 是 | struct, interface | 是 | 是 | - |
| Java | 是 | class, interface, enum | 是 | 是 | - |
| Scala | 是 | class, object | 是 | 是 | - |
| C# | 是 | class, namespace | 是 | - | - |
| PHP | 是 | class | 是 | - | - |
| Lua | 是 | - | 是 | - | - |

### 图谱模式

**节点**：`Project`、`Package`、`Module`、`File`、`Folder`、`Class`、`Function`、`Method`、`Type`、`Enum`、`Union`

**关系**：`CONTAINS_*`、`DEFINES`、`DEFINES_METHOD`、`CALLS`、`INHERITS`、`IMPLEMENTS`、`IMPORTS`、`OVERRIDES`

**属性**：`qualified_name`（主键）、`name`、`path`、`start_line`、`end_line`、`signature`、`return_type`、`visibility`、`parameters`、`kind`、`docstring`

### 架构

项目采用 5 层 harness 架构：

```
L4  entrypoints/         MCP 服务器、CLI 命令行
L3  domains/upper/       API 文档、RAG、引导、调用链追踪
L2  domains/core/        图谱、向量嵌入、搜索
L1  foundation/          解析器、服务、工具
L0  foundation/types/    常量、模型、类型定义
```

### 环境变量

#### LLM（优先匹配）

| 变量 | 用途 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | 通用 LLM 密钥（最高优先级） | - |
| `LLM_BASE_URL` | API 端点 | `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名称 | `gpt-4o` |
| `OPENAI_API_KEY` | OpenAI 或兼容服务 | - |
| `MOONSHOT_API_KEY` | Moonshot / Kimi（旧版） | - |

#### Embedding

| 变量 | 用途 | 默认值 |
|---|---|---|
| `DASHSCOPE_API_KEY` | DashScope（Qwen3 Embedding） | - |
| `DASHSCOPE_BASE_URL` | DashScope 端点 | `https://dashscope.aliyuncs.com/api/v1` |

#### 系统

| 变量 | 用途 | 默认值 |
|---|---|---|
| `TERRAIN_WORKSPACE` | 工作区目录 | `~/.terrain` |

### 安装选项

#### 从 PyPI 安装

```bash
# 核心功能（含 C/C++、Python、JS/TS 语法支持）
pip install terrain-ai

# 包含所有语言语法（Rust、Go、Java、Scala、Lua）
pip install "terrain-ai[treesitter-full]"
```

#### 从本地源码安装

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

pip install ".[treesitter-full]"

# 或以可编辑模式安装（开发用）
pip install -e ".[treesitter-full]"
```

#### 构建并安装 wheel 包

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

python3 -m build
pip install dist/terrain_ai-*.whl
```

### 开发

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
pip install -e ".[treesitter-full]"

python3 -m pytest tests/ -v

# 集成测试（需要 tinycc 仓库在 ../tinycc）
python3 -m pytest tests/domains/core/test_graph_build.py -v      # ~3 分钟
python3 -m pytest tests/domains/upper/test_api_docs.py -v        # ~3 分钟
python3 -m pytest tests/domains/core/test_step3_embedding.py -v  # ~27 分钟（API 调用）
python3 -m pytest tests/domains/upper/test_api_find_integration.py -v  # ~47 分钟（完整流水线）
```

## 许可证

Apache License 2.0 — 详见 [LICENSE](LICENSE)。
