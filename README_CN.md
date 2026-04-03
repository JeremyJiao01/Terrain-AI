# Code Graph Builder

[English](README.md) | 中文

从任意代码仓库构建知识图谱，自动生成 API 文档，支持语义化代码搜索 —— 一切通过 MCP 服务暴露给 AI 编程助手。

## 功能概览

```
你的代码仓库
    |
    v
[Tree-sitter AST 解析]  ──>  知识图谱 (Kuzu)
    |                               |
    |                               v
    |                        API 文档 (Markdown)
    |                               |
    |                               v
    |                        向量嵌入
    |                               |
    v                               v
MCP 服务器  <──────────────  语义搜索
    |
    v
Claude Code / Cursor / Windsurf / 任何 MCP 客户端
```

**AI Agent 的核心工作流：**

```
initialize_repository  →  find_api  →  get_api_doc
```

1. 索引代码仓库（一次性）
2. 通过模糊语义描述搜索（如 "PWM 占空比更新"）
3. 获取精确的函数签名、调用树和使用示例

## 快速开始

### 通过 npx 安装（推荐）

```bash
# 首次运行 — 交互式配置向导
npx code-graph-builder@latest --setup

# 启动 MCP 服务器
npx code-graph-builder@latest --server
```

配置向导会：
1. 自动安装 Python 包（如未安装）
2. 配置工作目录、LLM 和 Embedding 提供商
3. 运行 MCP 冒烟测试验证服务器可用
4. 可选注册为 Claude Code 全局 MCP 服务器（`claude mcp add --scope user`）

### 通过 pip 安装

```bash
pip install "code-graph-builder[treesitter-c,semantic]"
cgb-mcp  # 启动 MCP 服务器
```

### 卸载

```bash
npx code-graph-builder@latest --uninstall
```

移除：Claude MCP 注册、Python 包、工作区数据。

### MCP 客户端配置

添加到你的 MCP 客户端配置（Claude Code、Cursor、Windsurf 等）：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "npx",
      "args": ["-y", "code-graph-builder@latest", "--server"]
    }
  }
}
```

Windows 平台使用：

```json
{
  "mcpServers": {
    "code-graph-builder": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "code-graph-builder@latest", "--server"]
    }
  }
}
```

## 流水线

| 步骤 | 内容 | 输入 | 输出 |
|------|------|------|------|
| 1. graph-build | Tree-sitter AST 解析 | 源代码 | Kuzu 图数据库 |
| 2. api-doc-gen | 查询图谱，渲染文档 | 图谱 | 三级 Markdown（索引 / 模块 / 函数） |
| 2b. desc-gen | LLM 生成描述 | 缺少文档字符串的函数 | L3 Markdown 中的描述 |
| 3. embed-gen | 函数文档向量化 | L3 Markdown 文件 | 向量存储 (pickle) |

步骤 1-3 通过 `initialize_repository` 自动执行。Wiki 生成可通过 `generate_wiki` 单独使用。

```
initialize_repository  →  步骤 1-3（完整流水线）
build_graph            →  仅步骤 1
generate_api_docs      →  步骤 2 + 2b（模式：full / resume / enhance）
rebuild_embeddings     →  步骤 3
generate_wiki          →  独立功能（不在主流水线中）
```

### API 文档生成模式

| 模式 | 行为 |
|------|------|
| `full` | 从图谱重建所有文档 |
| `resume` | 仅为含 TODO 占位符的函数生成 |
| `enhance` | LLM 驱动的模块摘要 + API 使用工作流 |

## MCP 工具

### 主要工具（10 个暴露）

#### 仓库管理
| 工具 | 说明 |
|------|------|
| `initialize_repository` | 索引仓库：图谱 + API 文档 + 嵌入向量 |
| `get_repository_info` | 当前仓库统计（节点/关系数量、服务状态） |
| `list_repositories` | 所有已索引仓库及流水线完成状态 |
| `switch_repository` | 切换当前查询的仓库 |
| `link_repository` | 复用已有索引到其他仓库路径（无需重新索引） |

#### 代码搜索与文档
| 工具 | 说明 |
|------|------|
| `find_api` | 语义搜索 + API 文档（主要搜索工具） |
| `list_api_docs` | 浏览 L1 模块索引或 L2 模块详情 |
| `get_api_doc` | L3 函数详情：签名、调用树、使用示例、源码 |
| `generate_api_docs` | 生成/更新 API 文档（full / resume / enhance） |
| `get_config` | 显示服务器配置和服务可用性 |

### 隐藏工具（11 个可通过 handler 访问）

这些工具已被上述 API 文档工作流取代，但仍可访问：
`query_code_graph`、`get_code_snippet`、`semantic_search`、`locate_function`、`list_api_interfaces`、`list_wiki_pages`、`get_wiki_page`、`generate_wiki`、`rebuild_embeddings`、`build_graph`、`prepare_guidance`

## API 文档格式

生成的文档同时针对 AI Agent 阅读和向量检索进行了优化。

### L3 函数详情（嵌入单元）

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

## 参数与内存

| 参数 | 方向 | 所有权 |
|------|------|--------|
| `CType *type` | 入/出 | 借用，可修改 |
| `AttributeDef *ad` | 入/出 | 借用，可修改 |

## 实现

​```c
int parse_btype(CType *type, AttributeDef *ad, int ignore_label) {
    // ... 内嵌源代码
}
​```
```

### C/C++ 特有功能

- 提取函数上方的 `//` 和 `/* */` 注释作为描述
- 显示 struct/union/enum 成员及类型
- 宏定义独立章节
- static/public/extern 可见性分类
- 从签名推断内存所有权
- 头文件/实现文件分离
- 通过 `#include` 头文件映射实现跨文件函数调用解析
- 支持 GB2312/GBK 编码的源文件

## 支持的语言

| 语言 | 函数 | 类/结构体 | 调用 | 导入 | 类型 |
|------|------|-----------|------|------|------|
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

## 图谱模式

**节点**：`Project`、`Package`、`Module`、`File`、`Folder`、`Class`、`Function`、`Method`、`Type`、`Enum`、`Union`

**关系**：`CONTAINS_*`、`DEFINES`、`DEFINES_METHOD`、`CALLS`、`INHERITS`、`IMPLEMENTS`、`IMPORTS`、`OVERRIDES`

**属性**：`qualified_name`（主键）、`name`、`path`、`start_line`、`end_line`、`signature`、`return_type`、`visibility`、`parameters`、`kind`、`docstring`

## 环境变量

### LLM（优先匹配）

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | 通用 LLM 密钥（最高优先级） | - |
| `LLM_BASE_URL` | API 端点 | `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名称 | `gpt-4o` |
| `OPENAI_API_KEY` | OpenAI 或兼容服务 | - |
| `MOONSHOT_API_KEY` | Moonshot / Kimi（旧版） | - |

### Embedding

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | DashScope（Qwen3 Embedding） | - |
| `DASHSCOPE_BASE_URL` | DashScope 端点 | `https://dashscope.aliyuncs.com/api/v1` |

### 系统

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `CGB_WORKSPACE` | 工作区目录 | `~/.code-graph-builder` |

## 安装选项

### 从 PyPI 安装

```bash
# 仅核心功能（图谱构建）
pip install code-graph-builder

# 包含所有语言语法
pip install "code-graph-builder[treesitter-full]"
```

### 从本地源码安装

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

# 安装所有语言语法
pip install ".[treesitter-full]"

# 或以可编辑模式安装（开发用）
pip install -e ".[treesitter-full]"
```

### 构建并安装 wheel 包

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki

# 构建 wheel 和 sdist
python3 -m build

# 安装 wheel
pip install dist/code_graph_builder-*.whl

# 或强制重新安装
pip install --force-reinstall dist/code_graph_builder-*.whl
```

## 开发

```bash
git clone https://github.com/JeremyJiao01/CodeGraphWiki.git
cd CodeGraphWiki
pip install -e ".[treesitter-full]"

# 运行测试
python3 -m pytest code_graph_builder/tests/ -v

# 集成测试（需要 tinycc 仓库在 ../tinycc）
python3 -m pytest code_graph_builder/tests/test_step1_graph_build.py -v   # ~3 分钟
python3 -m pytest code_graph_builder/tests/test_step2_api_docs.py -v      # ~3 分钟
python3 -m pytest code_graph_builder/tests/test_step3_embedding.py -v     # ~27 分钟（API 调用）
python3 -m pytest code_graph_builder/tests/test_api_find_integration.py -v # ~47 分钟（完整流水线）
```

## 许可证

MIT
