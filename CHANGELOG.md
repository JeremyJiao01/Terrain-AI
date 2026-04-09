# Changelog / 更新日志

---

## [Unreleased]

### Added
- `npx code-graph-builder --setup` wizard: new **Step 4 — Language Support** for selecting additional language parsers (Rust, Go, Java, Lua, Scala)
  - Core languages (Python, JS, TS, C, C++) shown as pre-checked and locked (cannot be deselected)
  - Optional languages default to unchecked; user selects with Space, confirms with Enter
  - pip install command dynamically includes only selected packages
  - Setup complete summary lists installed parsers and reminds how to add more: re-run `--setup`
- `cgb status` now shows a **parsers** line listing which tree-sitter language packages are installed, with a hint to run `--setup` for missing ones

### Changed
- `npx code-graph-builder --server` (auto-install path) now installs `code-graph-builder[treesitter-full]` so all language parsers are available out of the box

---

### 新增
- `npx code-graph-builder --setup` 向导新增 **Step 4 — Language Support** 语言支持选择步骤
  - 核心语言（Python、JS、TS、C、C++）以灰色 [x] 锁定显示，无法取消
  - 可选语言（Rust、Go、Java、Lua、Scala）默认全部未选，空格切换，回车确认
  - pip 安装命令根据选择动态拼接，只安装用户勾选的语言包
  - 安装完成摘要显示已安装的 parser 列表，并提示通过重新运行 `--setup` 添加更多语言
- `cgb status` 新增 **parsers** 行，显示当前已安装的 tree-sitter 语言包，缺失时给出修复提示

### 变更
- `npx code-graph-builder --server`（自动安装路径）改为安装 `code-graph-builder[treesitter-full]`，开箱即支持全部语言解析器

---

## [0.43.0] — 2026-04-09

### Changed
- `cgb index --incremental/-i` renamed to `cgb index --update/-u` for brevity
- Remove `code-graph-builder` entry point from Python package to avoid conflict with npm CLI; use `cgb` instead

### Added
- `cgb index` now supports repo-local `.cgb/` output: interactive menu or `--output .cgb` flag lets you store index alongside the repo instead of the global workspace
- CLI `_load_repos` prefers repo-local `.cgb/` over global workspace when both exist
- MCP `_try_auto_load` prefers repo-local `.cgb/` over global workspace when both exist
- `cgb reload` command — hot-reload `.env` configuration and display changes
- `python -m code_graph_builder` entry point (`__main__.py`) as fallback when `cgb` is not on PATH
- Windows: `npx code-graph-builder --setup` now auto-adds Python Scripts directory to user PATH
- Python 3.10 compatibility: `StrEnum` polyfill for `enum` module

---

### 变更
- `cgb index --incremental/-i` 重命名为 `cgb index --update/-u`，更简短
- 移除 Python 包的 `code-graph-builder` 入口点，避免与 npm CLI 冲突；统一使用 `cgb`

### 新增
- `cgb index` 支持 repo-local `.cgb/` 输出：交互菜单或 `--output .cgb` 标志可将索引存储在仓库旁而非全局 workspace
- CLI `_load_repos` 优先加载 repo-local `.cgb/`，其次才是全局 workspace
- MCP `_try_auto_load` 优先加载 repo-local `.cgb/`，其次才是全局 workspace
- `cgb reload` 命令 — 热重载 `.env` 配置并显示变更
- `python -m code_graph_builder` 入口点（`__main__.py`），在 `cgb` 不在 PATH 时可作为备选
- Windows：`npx code-graph-builder --setup` 现在自动将 Python Scripts 目录添加到用户 PATH
- Python 3.10 兼容性：为 `enum` 模块添加 `StrEnum` polyfill

---

## [0.41.0] — 2026-04-08

### Changed
- `cgb index` no longer generates wiki by default; add `--wiki` flag to opt-in
- `cgb rebuild` skips wiki by default; add `--wiki` flag or use `--step wiki`
- Hide `initialize_repository` MCP tool from clients; users should index via `cgb index`
- All MCP error messages now reference `cgb index <path>` instead of `initialize_repository`

### Fixed
- Arrow-key menu rendering (`cgb config`, `cgb repo`) — initial render now runs inside `tty.setraw()` so `\033[{N}A` cursor-up line count is consistent between initial and subsequent draws; raw mode uses explicit `\r\n` (CR+LF) since OPOST/ONLCR is disabled
- LLM client: defensive parsing for null message/content in API responses; surface API-level errors returned with HTTP 200; handle MiniMax-style `base_resp` error codes

### Added
- `cgb index` prompts for custom display name; `cgb repo` offers rename after switch
- `save_meta()` accepts optional `repo_name`, preserves custom names across re-index

---

### 变更
- `cgb index` 默认不再生成 wiki；通过 `--wiki` 标志启用
- `cgb rebuild` 默认跳过 wiki；通过 `--wiki` 标志或 `--step wiki` 启用
- 隐藏 `initialize_repository` MCP 工具；用户应通过 `cgb index` 索引
- 所有 MCP 错误提示现在引用 `cgb index <path>` 而非 `initialize_repository`

### 修复
- 箭头键菜单渲染（`cgb config`、`cgb repo`）— 初始渲染现在在 `tty.setraw()` 内执行，确保 `\033[{N}A` 光标上移行数在初始和后续绘制间一致；raw 模式使用显式 `\r\n`（CR+LF），因为 OPOST/ONLCR 已禁用
- LLM 客户端：对 API 响应中的 null message/content 进行防御性解析；暴露 HTTP 200 返回的 API 级别错误；处理 MiniMax 风格的 `base_resp` 错误码

### 新增
- `cgb index` 提示输入自定义显示名称；`cgb repo` 切换后可重命名
- `save_meta()` 支持可选 `repo_name` 参数，重新索引时保留自定义名称

---

## [0.37.0] — 2026-04-08

### Added
- `cgb link <path> [--db <name>]` — associate a local repo with shared pre-built artifacts (graph.db, vectors.pkl, api_docs/, wiki/); supports interactive selector, symlinks for zero disk overhead, and per-repo meta.json
- Auto-refresh `.env` before each LLM/embedding service creation — `refresh_env()` re-reads the workspace `.env` only when mtime changes, so `cgb config` edits take effect without process restart

### Fixed
- `cgb link` now handles Windows-style paths pasted from File Explorer address bar (e.g. `C:\Users\john\project`); strips quotes/whitespace and uses `PureWindowsPath` for cross-OS correctness

---

### 新增
- `cgb link <path> [--db <name>]` — 将本地仓库关联到共享的预构建产物（graph.db、vectors.pkl、api_docs/、wiki/）；支持交互式选择、符号链接零磁盘开销、每仓库独立 meta.json
- 每次创建 LLM/Embedding 服务前自动刷新 `.env` — `refresh_env()` 仅在文件修改时间变化时重新读取，`cgb config` 修改后无需重启进程

### 修复
- `cgb link` 现在可正确处理从 Windows 资源管理器地址栏粘贴的路径（如 `C:\Users\john\project`）；自动去除引号和空白，使用 `PureWindowsPath` 实现跨平台兼容

---

## [0.36.0] — 2026-04-08

### Added
- `cgb config` interactive wizard — arrow-key menu UI matching the npx `--setup` wizard style, supports both interactive and CLI flag modes
- Enhanced `cgb status` — displays workspace path, LLM model, embedding model, and provider info
- `/ask` and `/trace` skills — project-scoped Claude Code slash commands for quick queries and call chain tracing
- `reload_config` MCP tool — hot-reload settings without restarting the server
- Auto-install skills on `npx --setup` — copies slash commands to `.claude/commands/` during first-time setup

### Fixed
- MCP deadlock: prevent blocking git calls and Kuzu file locks during concurrent MCP operations
- `commands_cli.py` now loads workspace `.env` correctly (previously only loaded CWD `.env`)

---

### 新增
- `cgb config` 交互式配置向导 — 箭头键菜单 UI，与 npx `--setup` 风格一致，支持交互模式和 CLI 参数模式
- 增强 `cgb status` — 显示工作区路径、LLM 模型、Embedding 模型和提供商信息
- `/ask` 和 `/trace` 技能 — 项目级 Claude Code 斜杠命令，用于快速查询和调用链追踪
- `reload_config` MCP 工具 — 热重载配置，无需重启服务器
- `npx --setup` 自动安装技能 — 首次配置时自动将斜杠命令复制到 `.claude/commands/`

### 修复
- MCP 死锁：防止并发 MCP 操作时 git 调用和 Kuzu 文件锁阻塞
- `commands_cli.py` 现在正确加载工作区 `.env`（之前仅加载当前目录的 `.env`）

---

## [0.34.0] — 2026-04-08

### Added
- MCP `call_tool` lifecycle tracing: debug logs for main init, incremental sync, and handler timing
- `scripts/debug-mcp.sh` — MCP Inspector helper with `--tail` and `--raw-test` modes
- Incremental sync: read-only Kuzu connection for API doc cascade to avoid Windows file lock deadlocks

### Changed
- Noisy parser logs (definition_processor, structure_processor, utils) downgraded from info to debug
- Incremental updater explicitly releases Kuzu references and runs `gc.collect()` after graph update
- Debug logging outputs to `.log` only (removed redundant `.txt` sink)

### Fixed
- Windows Kuzu lock contention: cascade API doc regeneration now opens a temporary read-only connection instead of reusing the builder's connection

---

### 新增
- MCP `call_tool` 全生命周期追踪：main 初始化、增量同步、handler 计时的 debug 日志
- `scripts/debug-mcp.sh` — MCP Inspector 辅助脚本，支持 `--tail` 和 `--raw-test` 模式
- 增量同步：API 文档级联再生使用只读 Kuzu 连接，避免 Windows 文件锁死锁

### 变更
- 解析器噪音日志（definition_processor、structure_processor、utils）从 info 降级为 debug
- 增量更新器在图更新后显式释放 Kuzu 引用并执行 `gc.collect()`
- Debug 日志仅输出到 `.log` 文件（移除了冗余的 `.txt` 输出）

### 修复
- Windows Kuzu 锁竞争：级联 API 文档再生改为打开临时只读连接，不再复用 builder 的连接

---

## [0.30.0] — 2026-04-06

### Added
- `/code-gen` skill — 4-stage MCP workflow for implementation planning from design documents
  - Phase 1: Concept extraction from design doc
  - Phase 2: Broad search via `find_api`
  - Phase 3: Deep research via `get_api_doc`, `find_callers`, `trace_call_chain`, `list_api_docs`
  - Phase 3.5: Gap check with one-round backfill
  - Phase 4: Structured implementation plan output (stop and wait for user confirmation)
- `.claude/commands/` directory tracked in git for project-scoped custom commands

---

## [0.29.0] — 2026-04-04 ~ 2026-04-06

### Added
- `cgb` CLI command with workspace management, multi-repo listing, and progress bar
- Call chain trace feature: new L3 `calltrace` domain + `get_call_chain` MCP tool
- C/C++ function pointer tracking: detects assignments, resolves indirect calls through `func_ptr_map`
- `process_func_ptr_assignments` pipeline step wired into graph build
- `func_ptr_assign` tree-sitter query for C/C++ function pointer detection
- GitHub Actions CI workflow (dep-check + pytest)
- Layer dependency checker `tools/dep_check.py` with tests
- Impact-based testing rules and feature checklists in contributing docs

### Changed
- Full 5-layer harness architecture (L0–L4):
  - L0 `foundation/types/` — pure data definitions
  - L1 `foundation/{parsers,services,utils}/` — shared infrastructure
  - L2 `domains/core/` — graph, embedding, search
  - L3 `domains/upper/` — apidoc, rag, guidance, calltrace
  - L4 `entrypoints/` — mcp, cli
- Tests reorganized by layer, all compatibility shims removed
- Contributing guides added: architecture, testing, add-feature, add-language

### Fixed
- CI: Python 3.10 dropped from matrix (StrEnum requires 3.11+)
- CI: unstable tests excluded (LLM-dependent, broken-assertion, pre-existing model mismatch)

---

### 新增
- `cgb` 命令行工具，支持工作区管理、多仓库列表和进度条显示
- 调用链追踪功能：新增 L3 `calltrace` 域及 `get_call_chain` MCP 工具
- C/C++ 函数指针追踪：检测赋值语句，通过 `func_ptr_map` 解析间接调用
- `process_func_ptr_assignments` 步骤接入图构建流水线
- 新增 `func_ptr_assign` tree-sitter 查询，用于 C/C++ 函数指针检测
- GitHub Actions CI 工作流（依赖检查 + pytest）
- 层级依赖检查工具 `tools/dep_check.py` 及配套测试
- 贡献文档中新增影响域测试规则和功能开发检查清单

### 变更
- 完整的 5 层 harness 架构（L0–L4）：
  - L0 `foundation/types/` — 纯数据定义
  - L1 `foundation/{parsers,services,utils}/` — 共享基础设施
  - L2 `domains/core/` — 图、向量、搜索
  - L3 `domains/upper/` — apidoc、rag、guidance、calltrace
  - L4 `entrypoints/` — mcp、cli
- 测试按层级重新组织，移除全部兼容垫片
- 新增贡献文档：架构规范、测试规范、功能开发、添加语言支持

### 修复
- CI：移除 Python 3.10（StrEnum 需要 3.11+）
- CI：排除不稳定测试（依赖 LLM、断言错误、模型名不匹配）

---

## [0.28.0] — 2026-04-03

### Added
- `find_callers` MCP tool for direct call graph queries
- Hybrid keyword + vector search to reduce query sensitivity
- Chinese README (`README_CN.md`)

### Fixed
- Preserve embedding config when `--setup` is skipped
- Hot-reload env vars before `initialize` to pick up runtime changes

---

### 新增
- `find_callers` MCP 工具，直接查询函数的调用方
- 混合关键词 + 向量检索，降低查询敏感性
- 中文 README（`README_CN.md`）

### 修复
- 跳过 `--setup` 时保留已有的 embedding 配置
- `initialize` 前热重载环境变量，确保运行时修改生效

---

## [0.27.0] — 2026-04-03

### Added
- `rebuild_embeddings` exposed as a standalone MCP tool (previously only internal)

---

### 新增
- `rebuild_embeddings` 作为独立 MCP 工具对外暴露（此前仅内部使用）

---

## [0.26.0] — 2026-04-03

### Changed
- npm package cleanup and install script adjustments

---

### 变更
- npm 包清理与安装脚本调整

---

## [0.25.0] — 2026-04-02

### Fixed
- Add `__len__` to `BoundedASTCache` to fix `len()` calls
- Harden UTF-8 decoding across all tree-sitter and metadata file paths

---

### 修复
- 为 `BoundedASTCache` 补充 `__len__` 方法，修复 `len()` 调用报错
- 加固所有 tree-sitter 及元数据文件路径的 UTF-8 解码逻辑

---

## [0.24.0] — 2026-04-02

### Added
- `CGB_DEBUG` environment variable for verbose debug logging

### Fixed
- Normalize GBK/CRLF line endings before passing source to tree-sitter parser

---

### 新增
- `CGB_DEBUG` 环境变量，开启详细调试日志

### 修复
- 传给 tree-sitter 前规范化 GBK/CRLF 换行符

---

## [0.23.0] — 2026-04-02

### Fixed
- `find_api` `AttributeError` when processing API doc results
- Remove hardcoded PyPI mirror index; always use official PyPI

---

### 修复
- 修复 `find_api` 处理 API 文档结果时的 `AttributeError`
- 移除硬编码的 PyPI 镜像源，统一使用官方 PyPI

---

## [0.22.0] — 2026-04-01

Minor version bump, internal housekeeping.

---

小版本号更新，内部整理。

---

## [0.21.0] — 2026-04-01

### Fixed
- Ensure `.c` file functions are included in API docs
- Exclude macros from `funcs/` directory in API doc output
- Escape backslashes and newlines in Cypher queries; upgrade error logging
- Align `fetch_all_calls` with `_build_call_graph` expected 7-column format
- Add `path` property to `Function`, `Class`, `Type`, and `Macro` graph nodes

### Changed
- Remove generated dist files from git tracking
- Use official PyPI index for `pip install` to avoid mirror sync delays

---

### 修复
- 确保 `.c` 文件中的函数被正确写入 API 文档
- API 文档的 `funcs/` 目录中排除宏定义
- 转义 Cypher 查询中的反斜杠和换行符；升级错误日志
- `fetch_all_calls` 返回格式与 `_build_call_graph` 期望的 7 列格式对齐
- 为图节点 `Function`、`Class`、`Type`、`Macro` 补充 `path` 属性

### 变更
- 从 git 跟踪中移除生成的 dist 文件
- `pip install` 使用官方 PyPI，避免镜像同步延迟

---

## [0.16.0] — 2026-03-30

### Added
- GB2312/GBK encoding support for reading source files with non-UTF-8 content
- Resolve C cross-file function calls via `#include` header mapping
- Repo-wide filename search for C header resolution

### Fixed
- LiteLLM proxy: switch backend from `httpx` to `requests` with correct `Bearer` auth header
- Adapt `Qwen3Embedder` for third-party OpenAI-compatible embedding providers

---

### 新增
- 支持读取 GB2312/GBK 编码的源文件
- 通过 `#include` 头文件映射解析 C 语言跨文件函数调用
- 使用全仓库文件名搜索解析 C 头文件（更简洁、更健壮）

### 修复
- LiteLLM：后端从 `httpx` 切换到 `requests`，修复 `Bearer` 认证头
- `Qwen3Embedder` 适配第三方 OpenAI 兼容的 embedding 服务

---

## [0.15.0] — 2026-03-29

### Added
- Parallel API doc generation for faster indexing
- Generate embeddings from markdown content (not just source code)
- Enhance mode: enrich existing API docs incrementally
- `code-gen` skill integration
- LiteLLM proxy auto-detected as LLM provider
- LLM connection test script in setup wizard

---

### 新增
- 并行生成 API 文档，大幅提升索引速度
- 从 markdown 内容生成 embedding（不再局限于源码）
- Enhance 模式：增量更新已有 API 文档
- 集成 `code-gen` skill
- LiteLLM 代理自动识别为 LLM 提供商
- setup wizard 中新增 LLM 连接测试脚本

---

## [0.12.0] — 2026-03-27 ~ 2026-03-28

### Fixed
- Explicitly call `conn.close()` and `db.close()` to release KuzuDB file locks after pipeline

---

### 修复
- 流水线结束后显式调用 `conn.close()` 和 `db.close()`，释放 KuzuDB 文件锁

---

## [0.8.0] — 2026-03-26

### Added
- `auto-install`, `uninstall`, and MCP smoke test commands to `cli.mjs`
- Real usage examples extracted from call graph and added to API docs
- Enrich LLM description generation context with call graph and docstrings
- Windows compatibility for `cli.mjs`

### Fixed
- Prioritize pip-installed package over `uvx`/`pipx` when starting the server
- Make postinstall script resilient to missing `pip` command
- Enrich caller paths from `func_lookup` for usage example extraction
- Pass `repo_path` to `api-doc-gen` and fix source code embedding alignment

### Changed
- Switch to read-only KuzuDB connection after graph build for all query steps
- Move `requests`, `httpx`, and common tree-sitter grammars to core dependencies

---

### 新增
- `cli.mjs` 新增 `auto-install`、`uninstall` 及 MCP 冒烟测试命令
- 从调用图中提取真实使用示例并写入 API 文档
- 为 LLM 描述生成提供调用图与 docstring 上下文
- `cli.mjs` 兼容 Windows 系统

### 修复
- 启动服务时优先使用 pip 安装的包，而非 `uvx`/`pipx`
- postinstall 脚本在 `pip` 不存在时不再报错
- 从 `func_lookup` 补充调用方路径，用于提取使用示例
- 将 `repo_path` 传递给 `api-doc-gen`，修复源码 embedding 对齐问题

### 变更
- 图构建完成后所有查询步骤切换为只读连接
- 将 `requests`、`httpx` 及常用 tree-sitter 语法包迁入核心依赖

---

## [0.4.0] — 2026-03-24

### Added
- Root `package.json` for local npm install development workflow
- `get_config` MCP tool to show current server configuration
- Automatic retry with exponential backoff for KuzuDB lock errors

### Fixed
- Run setup wizard automatically after `npm install` for first-time guidance
- Use correct `Authorization: Bearer` header in LLM backend
- Close MCP connection before `build_graph` to prevent lock contention
- Reuse single DB connection across pipeline to prevent lock contention
- LLM description generation was silently failing on errors

---

### 新增
- 根目录 `package.json`，支持本地 npm install 开发流程
- `get_config` MCP 工具，展示当前服务器配置
- KuzuDB 锁错误时自动指数退避重试

### 修复
- `npm install` 后自动运行 setup wizard，引导首次配置
- LLM 后端使用正确的 `Authorization: Bearer` 请求头
- `build_graph` 前关闭 MCP 连接，避免锁竞争
- 流水线全程复用单个 DB 连接，避免锁竞争
- LLM 描述生成在出错时静默失败，现改为正确报错

---

## [0.3.0] — 2026-03-22 ~ 2026-03-23

### Added
- Universal MCP server packaging: `npx` and `pip install` both supported
- Interactive setup wizard for API key, LLM, and embedding provider configuration
- Auto-detect LLM provider from environment variables
- Hierarchical API documentation generation (L1/L2/L3 index structure)
- `find_api` MCP tool: natural language API search with doc aggregation
- `/api-find` custom command for Claude Code
- `/wiki-gen` standalone wiki generation command
- Global command installation via `~/.claude/commands/code-graph/`
- Load LLM/embedding config from `~/.claude/settings.json`
- Repo discovery and switching for cross-session workflows
- `prepare_guidance` MCP tool with LLM tool-calling support
- Provider selection (LLM + embedding) in setup wizard
- Extract C/C++ comments as docstrings; LLM description fallback
- Rewrite API doc template for C/C++ with embedding-optimized format
- Integration tests: MCP protocol, `api-find`, embedding pipeline

### Fixed
- Deduplicate API doc entries caused by duplicate graph relationships
- Populate `file_path`, `start_line`, `end_line` in semantic search results

### Changed
- Integrate API docs generation into graph build step (Step 1)
- Rename `KimiClient` → `LLMClient` for provider-neutral naming
- Remove 5 redundant MCP tools; merge stats into `get_repository_info`
- MCP error handling uses `ToolError` with `isError=True`

---

### 新增
- 通用 MCP 服务端打包：同时支持 `npx` 和 `pip install`
- 交互式 setup wizard，支持配置 API key、LLM 和 embedding 提供商
- 从环境变量自动识别 LLM 提供商
- 分层 API 文档生成（L1/L2/L3 索引结构）
- `find_api` MCP 工具：自然语言 API 搜索与文档聚合
- Claude Code 自定义命令 `/api-find`
- 独立 wiki 生成命令 `/wiki-gen`
- 通过 `~/.claude/commands/code-graph/` 全局安装自定义命令
- 从 `~/.claude/settings.json` 加载 LLM/embedding 配置
- 跨会话的仓库发现与切换功能
- `prepare_guidance` MCP 工具，支持 LLM tool-calling
- setup wizard 支持分别选择 LLM 和 embedding 提供商
- 提取 C/C++ 注释作为 docstring；无 docstring 时回退到 LLM 生成描述
- 重写 C/C++ API 文档模板，针对 embedding 检索优化格式
- 集成测试：MCP 协议、`api-find`、embedding 流水线

### 修复
- 去除因重复图关系导致的 API 文档重复条目
- 语义搜索结果中补全 `file_path`、`start_line`、`end_line` 字段

### 变更
- API 文档生成并入图构建步骤（Step 1）
- `KimiClient` 重命名为 `LLMClient`，去除厂商绑定
- 移除 5 个冗余 MCP 工具；统计信息合并入 `get_repository_info`
- MCP 错误处理改用 `ToolError` 并设置 `isError=True`

---

## [0.2.0] — 2026-02-26 ~ 2026-02-28

### Added
- Extract C API interfaces: function signatures, parameters, and visibility
- Enhanced C API extraction: structs, typedefs, macros, visibility modifiers
- Modular pipeline: independent steps with `repo-init` as orchestrator
- OpenAI-compatible embeddings support
- Enrich embedding text with docstring, file path, and call graph context
- Short-term optimizations: modular pipeline, better error messages

### Fixed
- `fetch_all` alias added to `KuzuIngestor` to satisfy `GraphServiceProtocol`
- Update `row["result"]` accesses to named-column dict format

---

### 新增
- 提取 C API 接口：函数签名、参数列表与可见性
- 增强 C API 提取：支持 struct、typedef、宏和可见性修饰符
- 模块化流水线：各步骤独立，由 `repo-init` 统一编排
- 支持 OpenAI 兼容的 embedding 接口
- embedding 文本中融合 docstring、文件路径和调用图上下文
- 短期优化：模块化流水线、更清晰的错误信息

### 修复
- `KuzuIngestor` 新增 `fetch_all` 别名，满足 `GraphServiceProtocol` 接口要求
- 将 `row["result"]` 访问方式更新为命名列字典格式

---

## [0.1.0] — 2026-02-24

Initial release.

- `code_graph_builder` library with code graph analysis and RAG support
- KuzuDB-backed code knowledge graph (functions, classes, types, macros, call edges)
- Tree-sitter AST parsing for Python, JavaScript, TypeScript, C, C++
- DeepWiki-style wiki generator with Mermaid diagram auto-fix
- MCP server module (`cgb-mcp`) for AI-assisted code navigation
- RAG pipeline with vector search over code graph nodes
- Windows setup guide for Claude Code

---

首次发布。

- `code_graph_builder` 库，提供代码图分析与 RAG 支持
- 基于 KuzuDB 的代码知识图谱（函数、类、类型、宏、调用边）
- Tree-sitter AST 解析，支持 Python、JavaScript、TypeScript、C、C++
- DeepWiki 风格的 wiki 生成器，自动修复 Mermaid 图表语法
- MCP 服务器模块（`cgb-mcp`），支持 AI 辅助代码导航
- 基于代码图节点的 RAG 流水线与向量检索
- Claude Code 的 Windows 安装指南
