# P0 产品官网 — 内容与交互设计规格

> Date: 2026-04-05
> Status: Approved

## 1. 目标

为 CodeGraphWiki（Code Graph Builder）构建一个静态产品官网，部署在 GitHub Pages。

核心目标：访客在 30 秒内理解"这是什么、为什么我需要它"，并在 5 分钟内通过交互体验感受到产品的核心价值，尤其是调用链追踪这一差异化能力。

## 2. 技术约束

- 纯静态 HTML + CSS + JS，无后端，无 API 调用
- 可部署在 GitHub Pages（`gh-pages` 分支或 `docs/` 目录）
- 所有交互数据（函数图谱、调用链、Wiki 内容）全部预置在 JS 里
- 不使用 React / Vue 等框架，保持零构建依赖

## 3. 页面结构

```
[Hero]
[痛点共鸣]
[交互模块 1：Pipeline 动画]
[交互模块 2：Trace Call 场景故事 + 动画 + 交互 Wiki 卡片]
[交互模块 3：调用链浏览器]
[快速开始]
[支持语言 & 兼容工具]
```

## 4. 各 Section 内容规格

### 4.1 Hero

**标题（一句话定位）：**
> "Your AI assistant finally understands your codebase."

**副标题：**
> "Code Graph Builder parses your source code into a knowledge graph — so Claude, Cursor, and Windsurf can find the exact function, call tree, and usage example they need."

**CTA：**
- 主按钮：`npx code-graph-builder@latest --setup`（带一键复制功能）
- 次按钮：View on GitHub

### 4.2 痛点共鸣

一段叙述性文字，不超过 3 句话：

> "Your AI assistant is brilliant — but it's navigating your codebase blind. It doesn't know your private APIs, your internal call chains, or which function actually handles that edge case. Code Graph Builder gives it a map."

### 4.3 交互模块 1 — Pipeline 动画

**触发方式：** 访客点击"▶ Run"按钮。

**动画内容：** 三步 pipeline 依次点亮，每步之间间隔 0.8 秒：

```
Step 1  graph-build     ██████████  ✓  Parsed 42 functions, 18 call relationships
Step 2  api-doc-gen     ██████████  ✓  Generated 42 function docs
Step 3  embed-gen       ██████████  ✓  Embeddings ready, semantic search enabled
```

**目的：** 让访客理解"索引一次，AI 永久可用"的核心机制。

### 4.4 交互模块 2 — Trace Call 场景故事

**叙事引言：**
> "A log error fires. You have a function name. You have no idea what triggered it."

**动画流程（访客点击"Start Investigation"触发，总时长约 5 秒）：**

| 帧 | 内容 |
|----|------|
| 第 1 帧 | 日志行高亮 → `ERROR in validate_checksum() at line 42` |
| 第 2 帧 | 工具扫描提示 → "Tracing call chain upward..." + 进度条 |
| 第 3 帧 | 调用路径浮现 → `main → process_packet → parse_frame → validate_checksum` |
| 第 4 帧 | Wiki 生成提示 → "Investigation worksheet generated ✓" |

**交互 Wiki 卡片（动画结束后出现）：**

卡片标题：`📄 validate_checksum — Investigation Worksheet`

四个可折叠节，默认第一节展开，其余折叠：

| 节名 | 预置示例内容 |
|------|------------|
| Call Chain | `main → process_packet → parse_frame → validate_checksum`，每跳附文件行号 |
| Trigger Conditions | AI 填充区域示例：`"Called when packet length field indicates data follows"` |
| Possible Causes | AI 填充区域示例：`"1. Corrupted packet data, 2. Mismatched checksum algorithm version"` |
| Related Functions | `parse_frame()`, `process_packet()`，点击后平滑滚动至调用链浏览器并高亮对应节点 |

**目的：** 展示 CodeGraphWiki 最核心的差异化能力——从日志报错直接生成 AI 可用的调查工作表，让开发者感受到"这不只是搜索，这是真正的代码理解"。

### 4.5 交互模块 3 — 调用链浏览器

**初始状态：** 展示一棵来自内置 toy C 项目的真实调用树，默认展开两层。

**交互方式：** 访客点击任意函数节点可展开/折叠其子调用。

**节点悬浮卡片内容：**
- 函数签名
- 所在文件 + 行号
- 被哪些函数调用

**数据来源：** 全部预置在 `demo-data.js` 中（约 50 个节点），为官网专门设计的静态 C 语言示例图谱，不依赖运行时构建。函数名、文件名、行号均真实可信，来自一个模拟的"网络报文解析器"小项目。

**目的：** 让访客用手感受调用链导航体验。

### 4.6 快速开始

两步安装流程：

```bash
# Step 1: 安装并初始化
npx code-graph-builder@latest --setup

# Step 2: 在 MCP 客户端配置（Claude Code / Cursor / Windsurf）
# 配置完成后直接向 AI 提问即可
```

附 MCP 配置 JSON 代码块（可复制）：

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

### 4.7 支持语言 & 兼容工具

- 语言支持表格：C/C++、Python、JavaScript/TypeScript、Rust、Go、Java、Scala、C#、PHP、Lua
- MCP 客户端兼容图标行：Claude Code、Cursor、Windsurf

## 5. 内容写作原则

- Hero 及所有叙述文字不出现技术术语（MCP、Kuzu、Tree-sitter）
- 代码示例使用真实输出，不用截图（不过时）
- 所有交互数据来自真实 toy 仓库，不造假

## 6. 文件组织

```
docs/site/           # 或根目录 /docs（取决于 GitHub Pages 配置）
├── index.html       # 单页，所有内容内联
├── style.css        # 样式
└── demo-data.js     # 预置的图谱数据、Wiki 内容
```

## 7. 不在范围内

- 后端服务、API、数据库
- 用户账号、分析统计
- 多语言版本（首版英文）
- 移动端适配（首版桌面优先）
