# 增量索引 + 代码变更感知 — 设计文档

**日期**：2026-04-06  
**状态**：已批准，待实现

---

## 背景与目标

当前"代码改了需要重新 init"的体验让用户放弃持续使用。目标是实现文件级增量更新，让工具变成一个持续存在的后台服务，而不是一次性工具。

**核心约束：**
- 触发方式：每次 MCP 工具调用前自动检查（用户无感知）
- 变更来源：只跟踪已 commit 的变更（`git diff <last_commit> HEAD`）
- 更新链路：图谱 → API 文档 → 向量嵌入（不含 Wiki）

---

## 架构总览

三个新组件，分别落在现有分层架构的不同层：

```
L1  foundation/services/git_service.py        GitChangeDetector
L2  domains/core/graph/incremental_updater.py  IncrementalUpdater
L4  entrypoints/mcp/server.py                  查询前拦截（in-process hook）
```

### 数据流

```
MCP 工具调用
    ↓
[L4] 查询前拦截 (_maybe_incremental_sync)
    ├─ 内存缓存命中 & HEAD 未变 → 直接执行工具（0ms）
    └─ HEAD 变了
          ↓
      [L1] GitChangeDetector
          git diff <last_commit> HEAD --name-only
          → 变更文件列表
          ↓
      [L2] IncrementalUpdater
          1. 查图：找 affected_callers（调用了变更文件函数的其他文件）
          2. 删除 (changed ∪ affected_callers) 的节点/关系
          3. Pass 2：重新解析 changed_files 的定义
          4. Pass 3：重新解析 (changed ∪ affected_callers) 的调用关系
          5. 更新 API 文档（变更模块）
          6. 更新向量嵌入（变更节点）
          7. 写入 meta.json: last_indexed_commit = HEAD
          ↓
      执行工具（返回最新结果）
```

### meta.json 新增字段

```json
{
  "last_indexed_commit": "abc1234",
  "indexed_at": "...",
  "wiki_page_count": 0
}
```

### MCP server 内存缓存

```python
_cached_head: str | None = None  # 进程级，避免重复 subprocess 调用
```

---

## 组件细节

### L1 · GitChangeDetector

**文件**：`foundation/services/git_service.py`

```python
class GitChangeDetector:
    def get_changed_files(
        self, repo_path: Path, last_commit: str | None
    ) -> tuple[list[Path], str]:
        """
        返回 (changed_files, current_head)
        last_commit 为 None 时返回空列表（表示首次索引，走全量）
        """
```

- `git rev-parse HEAD` 获取当前 HEAD
- 若 HEAD == last_commit → 返回 `([], HEAD)`（快速路径，无 subprocess）
- 否则 `git diff <last_commit> HEAD --name-only` → 过滤出 repo 内的有效文件
- last_commit 不在 git 历史中（force push、浅克隆）→ 降级全量重建

### L2 · IncrementalUpdater

**文件**：`domains/core/graph/incremental_updater.py`

```python
@dataclass
class IncrementalResult:
    files_reindexed: int
    callers_reindexed: int
    duration_ms: float

class IncrementalUpdater:
    def run(
        self,
        changed_files: list[Path],
        ingestor: KuzuIngestor,
        graph_updater: GraphUpdater,
        api_doc_step: Callable | None,
        embed_step: Callable | None,
    ) -> IncrementalResult:
        ...
```

**步骤 1 — 查询 affected_callers：**
```cypher
MATCH (caller:Module)-[:CALLS]->(callee:Function)
WHERE callee.file IN $changed_files
RETURN DISTINCT caller.file
```

**步骤 2 — 删除旧数据：**按 `source_file` 删除节点 + 关系，在 Kùzu 事务内执行（失败回滚，`last_indexed_commit` 不更新）

**步骤 3 — 重新解析：**
- Pass 2（定义）：只传入 `changed_files`
- Pass 3（调用关系）：传入 `changed_files ∪ affected_callers`

**步骤 4 — 级联更新：**调用现有 pipeline 步骤，限定范围为变更模块

### L4 · MCP 查询前拦截

**文件**：`entrypoints/mcp/server.py`

```python
_cached_head: str | None = None

async def call_tool(name, arguments):
    await _maybe_incremental_sync()
    return await _dispatch_tool(name, arguments)

async def _maybe_incremental_sync():
    global _cached_head
    current_head = git_detector.get_head()
    if current_head == _cached_head:
        return                                    # 0ms 快速路径
    changed, new_head = git_detector.get_changed_files(last_commit)
    if changed:
        incremental_updater.run(changed, ...)
    _cached_head = new_head
    meta.last_indexed_commit = new_head
```

---

## 边界情况与错误处理

| 情况 | 处理策略 |
|------|---------|
| `last_indexed_commit` 不在 git 历史中 | 捕获 git 错误 → 降级全量重建 |
| 非 git 仓库 | `git rev-parse` 失败 → 静默跳过增量检查，不报错 |
| git 命令不存在 | 同上 |
| 文件被删除 | 只删除图中对应节点/关系，不触发 Pass 2 |
| 二进制/不支持语言的文件 | `should_skip_path()` 过滤，忽略 |
| 增量更新中途崩溃 | Kùzu 事务回滚，`last_indexed_commit` 不更新，下次重试 |
| 变更文件数超过阈值（默认 50） | 自动降级为全量重建 |
| `meta.json` 无 `last_indexed_commit` 字段 | 跳过增量，等用户显式 init 后再启用 |

---

## 测试策略

### 单元测试

**`GitChangeDetector`** — `tests/foundation/test_git_service.py`
- mock subprocess，验证 HEAD 相同时返回空列表（快速路径）
- mock `git diff` 输出，验证文件路径过滤
- 模拟 git 命令失败，验证降级行为不抛异常

**`IncrementalUpdater`** — `tests/domains/core/test_incremental_updater.py`
- 用 Memory 后端，验证：
  - 变更文件的旧节点被删除
  - affected_callers 被正确识别和重新解析
  - `IncrementalResult` 计数准确
- 验证文件删除场景（节点删除，无 Pass 2）

### 集成测试

**`tests/entrypoints/test_incremental_sync.py`**

1. 全量 init → 记录 commit A
2. 修改 fixture 中一个文件 → git commit → 记录 commit B
3. 调用 `_maybe_incremental_sync()`
4. 验证图中对应节点已更新，其余节点不变

---

## 不在本次范围内

- Working tree（未 commit）的变更感知
- Wiki 页面的增量更新
- 非 git 仓库的 mtime 增量方案
- 后台 daemon / 文件系统 watcher
