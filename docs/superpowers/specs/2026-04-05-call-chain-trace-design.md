# Call Chain Trace — 向上调用链追踪设计规格

> Date: 2026-04-05
> Status: Approved

## 1. 目标

针对指定的目标函数，BFS 向上追溯完整调用链到入口点，以 tree 文本输出并生成**日志溯源调查工作表**（Wiki 页面），供 agent 后续结合源码填充触发条件、日志特征等细节。

## 2. 架构决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 代码放置层 | L3 `domains/upper/calltrace/` | 组合图查询+格式化+Wiki，属上层业务逻辑 |
| 图查询接口 | `GraphQueryService` + 扩展 | 复用已有 `fetch_callers()`，兼容性最强 |
| Wiki 集成 | 独立 `call-traces/` 子目录 | 与现有 wiki 体系松耦合 |
| 同名函数 | 各自独立输出 | 不同包的同名函数调用链无关 |
| 截断策略 | 按 entry point 分组限额 | 保证覆盖面 |
| 间接调用（函数指针） | Wiki 留白，tracer 不处理 | 图构建阶段能力边界，后续增强 |

## 3. 文件结构

```
domains/upper/calltrace/
├── __init__.py
├── tracer.py        # BFS 追溯 + 路径重建
├── formatter.py     # tree 文本格式化
└── wiki_writer.py   # Wiki 页面生成 + 文件写入
```

对现有代码的唯一改动：`domains/core/search/graph_query.py` 新增 `fetch_functions_by_name()` 方法。

## 4. 数据模型

定义在 `tracer.py` 中：

```python
@dataclass
class NodeInfo:
    qualified_name: str
    name: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None

@dataclass
class CallPath:
    nodes: list[NodeInfo]  # entry_point → ... → target，有序

    @property
    def depth(self) -> int:
        return len(self.nodes) - 1

@dataclass
class SingleTraceResult:
    """单个目标函数的追踪结果"""
    target: NodeInfo
    direct_callers: list[NodeInfo]
    entry_points: list[NodeInfo]
    paths: list[CallPath]
    max_depth_reached: bool
    truncated: bool  # 是否因限额截断

@dataclass
class TraceResult:
    """可能包含多个同名函数的完整结果"""
    results: list[SingleTraceResult]
    query_name: str  # 用户输入的原始查询名
```

## 5. 核心算法 — `tracer.py`

### 函数签名

```python
def trace_call_chain(
    query_service: GraphQueryService,
    target_function: str,
    max_depth: int = 10,
    paths_per_entry_point: int = 20,
) -> TraceResult:
```

### 流程

1. **定位 target 节点** — 先 `qualified_name` 精确匹配，再 fallback `name` 模糊匹配（通过新增的 `fetch_functions_by_name()`）。多个匹配各自独立处理。
2. **BFS 向上追溯** — 逐层调用 `query_service.fetch_callers()`。`visited: set[str]` 防环，到达 `max_depth` 或无 caller 时标记为 entry point。产出 `parent_map`、`node_info`、`entry_points`。
3. **DFS 路径重建** — 从每个 entry point 沿 `parent_map` 反向走到 target。每个 EP 最多 `paths_per_entry_point` 条路径，按深度升序排列。
4. **组装 SingleTraceResult**。

### 边界情况

| 场景 | 处理 |
|------|------|
| target 不存在 | 抛出 `ValueError` |
| target 无 caller | 返回空结果，`direct_callers=[]` |
| 循环调用 A→B→A | `visited` 防重，路径中不会出现环 |
| 达到 max_depth | 截断节点标记到 `entry_points`，设 `max_depth_reached=True` |
| 单 EP 路径 > 限额 | `truncated=True`，只保留前 N 条 |

### GraphQueryService 扩展

在 `graph_query.py` 新增：

```python
def fetch_functions_by_name(self, name: str) -> list[GraphNode]:
    """先 qualified_name 精确匹配，再 fallback name 匹配"""
```

## 6. 格式化 — `formatter.py`

### `format_tree(result: SingleTraceResult) -> str`

按 entry point 分组输出：

```
============================================================
  Call Chain Trace: LogSaveWithSubId
============================================================

Target: LogSaveWithSubId
  File: pkg/log/save.go:30
  Direct callers: 3
  Entry points: 2
  Total paths: 4

------------------------------------------------------------
  Entry Point 1: main (cmd/main.go:10)
------------------------------------------------------------

main()                                         cmd/main.go:10
└── initServer()                               cmd/init.go:25
    └── handleOrderCreate()                    api/order.go:85
        └── LogSaveWithSubId()                 pkg/log/save.go:30
```

规则：
- 函数名左对齐，文件位置右对齐（固定列宽 80）
- `└──` + 缩进表示层级
- 同一 EP 下多条路径按深度升序
- 截断时末尾追加 `... and N more paths from this entry point`

### `format_trace_result(result: TraceResult) -> str`

遍历 `result.results`，每个 `SingleTraceResult` 调用 `format_tree()`，多个同名函数之间用双分隔线隔开。

## 7. Wiki 页面 — `wiki_writer.py`

### 页面结构（日志溯源调查工作表）

```markdown
# Call Chain Trace: {target_function}

> Generated: {timestamp} | Repository: {repo_name}
> Status: 🔲 待填充 / 🔳 部分完成 / ✅ 已完成

## Overview

| Metric | Value |
|--------|-------|
| Target Function | `{target_function}` |
| File | `{target_file}:{target_line}` |
| Direct Callers | {count} |
| Entry Points | {count} |
| Total Paths | {count} |
| ⚠️ Indirect Calls | 本函数可能通过函数指针/回调被间接调用，见 Indirect Call Paths 段落 |

## Call Tree

\```
{自动生成的 tree 文本}
\```

## Entry Points Detail

### EP1: {entry_point_name} (`{file}:{line}`)

**Source Code:**
\```{lang}
{入口函数完整源码，自动填充}
\```

**触发场景：** <!-- FILL: 该入口函数在什么业务场景下被调用？ -->
**触发条件：** <!-- FILL: 触发需要满足什么前置条件？ -->
**调用频率：** <!-- FILL: 高频/低频/仅异常时？ -->

## Path Analysis

### Path 1: {entry_point} → ... → {target} (depth: N)

| # | Function | File | 触发条件 | 关键参数 | 日志输出 |
|---|----------|------|----------|----------|----------|
| 1 | `{ep_name}()` | `{file}:{line}` | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |
| ... | ... | ... | ... | ... | ... |

**路径摘要：** <!-- FILL: 这条完整路径对应什么业务流程？ -->
**异常分支：** <!-- FILL: 这条路径上有哪些 error handling 可能导致日志行为变化？ -->

## Indirect Call Paths (Function Pointer / Callback)

<!-- 以下记录通过函数指针、回调注册、结构体分发等间接方式到达 target 的调用路径 -->

| 注册函数 | 注册文件 | 结构体/字段 | 间接调用点 | 调用文件 |
|----------|----------|-------------|-----------|----------|
| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |

**注册模式描述：** <!-- FILL: 描述函数指针如何被注册到结构体、如何被传递、最终在哪里被间接调用 -->

## Log Fingerprint

| 日志特征 | 对应路径 | 对应函数 | 备注 |
|----------|----------|----------|------|
| <!-- FILL --> | <!-- FILL --> | <!-- FILL --> | <!-- FILL --> |

## Investigation Notes

<!-- FILL: agent 或人工补充的分析结论、排查记录 -->
```

### 源码获取

入口函数源码通过 `NodeInfo.path` + `start_line` + `end_line` 从仓库源文件直接读取：

```python
def _read_source_snippet(repo_root: Path, node: NodeInfo) -> str | None:
```

### 文件写入

- 路径：`{artifact_dir}/wiki/call-traces/trace-{target_name}.md`
- 同名函数多结果时用 qualified_name 的 hash 后缀区分
- 不更新 `index.md`，`call-traces/` 独立管理

## 8. MCP Tool 定义

### Tool: `trace_call_chain`

参数：
- `target_function: str`（必填）— 目标函数名，支持简名或全限定名
- `max_depth: int`（默认 10）— 最大向上追溯深度
- `save_wiki: bool`（默认 true）— 是否生成 Wiki 页面
- `paths_per_entry_point: int`（默认 20）— 每个入口点最大路径数

### Handler 返回值

```python
{
    "query": "LogSaveWithSubId",
    "matches": 1,
    "results": [
        {
            "target": "pkg.log.LogSaveWithSubId",
            "direct_callers": 3,
            "entry_points": 2,
            "total_paths": 4,
            "max_depth_reached": False,
            "truncated": False,
            "tree": "... tree text ...",
            "wiki_page": "path/to/trace-LogSaveWithSubId.md"
        }
    ]
}
```

## 9. 测试策略

```
tests/domains/upper/calltrace/
├── test_tracer.py       # 核心算法
├── test_formatter.py    # 格式化输出
└── test_wiki_writer.py  # Wiki 生成
```

**test_tracer.py** — 单层/多层/分叉/循环/不存在/无 caller/max_depth 截断/同名多匹配/路径超限额

**test_formatter.py** — 缩进对齐/多 EP 分组/截断提示

**test_wiki_writer.py** — 所有段落存在/FILL 标记存在/Indirect Call Paths 段落存在/文件路径正确

Mock `GraphQueryService` 返回预设数据，不依赖真实数据库。

## 10. 后续开发选项

完成本功能后，下一步可考虑：

- **函数指针追踪增强** — 在图构建阶段（L2 graph builder）识别 `struct.field = func` 赋值模式，建立 `REGISTERED_AS` 边，使 tracer 能自动追踪间接调用路径
