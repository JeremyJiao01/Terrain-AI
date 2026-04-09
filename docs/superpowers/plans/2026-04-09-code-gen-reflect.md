# Code-Gen 自迭代复盘系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `code-gen.md` skill 的阶段 0 和阶段 4 后分别注入"历史教训读取"和"复盘总结写入"，实现 `/code-gen` 的自迭代自更新。

**Architecture:** 纯 Markdown skill 文件修改，无 Python 代码变更。依赖 `get_config` MCP 工具（返回 `workspace.path` 字段）动态获取 cgb workspace 路径。三类产出以追加方式写入 `<workspace>/code-gen-memory/` 下的独立 MD 文件。

**Tech Stack:** Markdown（Claude Code custom command 格式），MCP 工具：`get_config`（已有）

---

### 前置信息：`get_config` 返回结构

已确认（`tools.py:2288-2292`）：

```json
{
  "workspace": {
    "path": "/absolute/path/to/workspace",
    "active_repo": "...",
    "active_artifact_dir": "..."
  }
}
```

skill 中通过 `get_config()` 调用后，从返回值的 `workspace.path` 字段获取 workspace 路径。

---

### Task 1：在阶段 0 末尾追加历史教训加载逻辑

**Files:**
- Modify: `/Users/jiaojeremy/.claude/commands/code-gen.md`（在"环境检查"节末尾追加）

- [ ] **Step 1：在 `code-gen.md` 的环境检查节末尾追加以下内容**

定位文件中"环境检查"节的末尾（当前最后一行是：`**解说**：简要向用户确认代码图谱已就绪……`），在其后追加：

```markdown

---

## 历史教训加载

调用 `get_config`，从返回值读取 `workspace.path`。

尝试读取 `<workspace>/code-gen-memory/lessons.md`：

- **文件存在且非空**：将全部内容注入本次调研上下文（作为"历史教训参考"）。向用户展示：
  > "已加载历史教训，本次调研将参考以下改进点：[列出文件末尾最新 5 条的 `## [日期] 标题` 摘要]"
- **文件不存在或为空**：静默跳过，不向用户提示。
```

- [ ] **Step 2：验证修改正确**

读取 `/Users/jiaojeremy/.claude/commands/code-gen.md`，确认：
1. "历史教训加载"节出现在"环境检查"节之后
2. "阶段 1：概念提取"节之前
3. 内容与上方完全一致，无截断

- [ ] **Step 3：Commit**

```bash
git add /Users/jiaojeremy/.claude/commands/code-gen.md
git commit -m "feat: code-gen phase 0 loads historical lessons from cgb workspace"
```

---

### Task 2：在阶段 4 之后追加阶段 5 复盘总结

**Files:**
- Modify: `/Users/jiaojeremy/.claude/commands/code-gen.md`（在"边界情况"节之前追加阶段 5）

- [ ] **Step 1：在 `code-gen.md` 的"边界情况"节之前追加以下完整内容**

定位文件中 `## 边界情况` 这一行，在其**之前**插入：

```markdown
---

## 阶段 5：复盘总结

阶段 4 方案输出完成后立即执行（无需等用户确认）。

### 5.0 获取 workspace 路径

调用 `get_config`，从返回值读取 `workspace.path`。

如果 `workspace.path` 为空或无效，输出：
> "未找到 cgb workspace，复盘已跳过。"
然后结束阶段 5，不写任何文件。

否则继续。目录 `<workspace>/code-gen-memory/` 如不存在则创建。

### 5.1 产出 1：错误教训

**目标文件**：`<workspace>/code-gen-memory/lessons.md`

回顾本次对话，提取：
- 搜索关键词选择失误（换词才找到正确结果）
- 遗漏接口（阶段 3.5 才发现的）
- 对设计文档理解偏差（用户纠正过的）
- 工具调用顺序或策略问题

**无错误时**：追加一行 `本次调研无新教训`（加上 `[YYYY-MM-DD]` 前缀）。

**有错误时**：追加以下格式到文件末尾：

```
## [YYYY-MM-DD] <简短标题>

**问题**：描述发生了什么
**教训**：下次应如何避免
```

### 5.2 产出 2：MCP 工具改进建议

**目标文件**：`<workspace>/code-gen-memory/improvements.md`

回顾本次对话，提取出现 2 次以上的"工具能力缺口"。

**无改进点时**：不写入（避免噪音）。

**有改进点时**：追加：

```
## [YYYY-MM-DD] <改进标题>

- **优先级**：高 / 中 / 低
- **问题描述**：工具在哪个场景表现不足
- **期望行为**：理想的工具输出是什么
```

### 5.3 产出 3：术语澄清语料

**目标文件**：`<workspace>/code-gen-memory/glossary.md`

提取用户在对话中对模糊表述给出的明确说明。

**文件不存在时**：先写入表头：

```
| 术语 | 澄清含义 | 来源日期 |
|------|---------|---------|
```

**有澄清内容时**：追加新行：

```
| <术语> | <用户给出的明确含义> | YYYY-MM-DD |
```

**无澄清内容时**：跳过 glossary.md，不写入。

### 5.4 展示复盘摘要

复盘完成后向用户展示：
> "复盘完成：写入了 N 条教训、M 条改进建议、K 条术语澄清，存储于 `<workspace>/code-gen-memory/`。"

```

- [ ] **Step 2：验证修改正确**

读取 `/Users/jiaojeremy/.claude/commands/code-gen.md`，确认：
1. `## 阶段 5：复盘总结` 出现在 `## 边界情况` **之前**
2. 阶段 5 包含完整的 5.0、5.1、5.2、5.3、5.4 子节
3. 原有的 `## 边界情况` 节内容完整保留，未被删除或截断

- [ ] **Step 3：Commit**

```bash
git add /Users/jiaojeremy/.claude/commands/code-gen.md
git commit -m "feat: code-gen phase 5 auto-reflection writes lessons/improvements/glossary"
```

---

## 自检：Spec 覆盖确认

| Spec 需求 | 对应任务 |
|-----------|---------|
| 阶段 0 读取历史教训，渐进式披露 | Task 1 |
| `get_config` 获取 workspace 路径 | Task 1 Step 1, Task 2 Step 1 |
| 阶段 4 后立即触发复盘（无需等确认） | Task 2 Step 1（阶段 5 开头已说明） |
| lessons.md 追加写入，带日期标签 | Task 2 Step 1（5.1） |
| improvements.md 追加写入，无改进时不写 | Task 2 Step 1（5.2） |
| glossary.md 追加表格行，无澄清时不写 | Task 2 Step 1（5.3） |
| 文件存储在 `<workspace>/code-gen-memory/` | Task 2 Step 1（5.0） |
| workspace 无效时跳过复盘并提示 | Task 2 Step 1（5.0） |
| lessons.md 展示最新 5 条摘要 | Task 1 Step 1 |
| 复盘完成后展示摘要 | Task 2 Step 1（5.4） |
