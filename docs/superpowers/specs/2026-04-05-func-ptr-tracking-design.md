# C/C++ 函数指针追踪增强 — 设计规格

> Date: 2026-04-05
> Status: Approved

## 1. 目标

在 C/C++ 图构建阶段，识别 `struct.field = func` 赋值模式，为间接调用点生成带 `indirect` 属性的 `CALLS` 边，使 calltrace 模块能自动追踪间接调用路径。

## 2. 范围

| 包含 | 排除 |
|------|------|
| `obj.field = func;`（点访问赋值） | 数组初始化 `{func_a, func_b}` |
| `ptr->field = func;`（箭头访问赋值） | 独立函数指针变量 `void (*fp)() = func;` |
| 同文件 & 跨文件检测 | 运行时动态赋值（条件分支内多次赋值取全部） |

## 3. 架构决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 关系类型 | 复用 `CALLS` + `indirect` 属性 | tracer 零改动，零新关系类型 |
| 代码放置 | `CallProcessor` 新增方法 | 与现有调用检测同层，共享 resolver |
| 检测时机 | call processing 阶段追加 | 依赖 function_registry 已完成 |
| 匹配策略 | Tree-sitter query | 精确控制 AST 节点类型 |

## 4. 文件结构

无新文件。改动范围：

| 文件 | 改动 |
|------|------|
| `foundation/types/constants.py` | 新增 `QUERY_FUNC_PTR_ASSIGN` 常量 |
| `foundation/parsers/call_processor.py` | 新增 `process_func_ptr_assignments()` 方法 |
| `foundation/parsers/call_resolver.py` | 新增 `func_ptr_map` 属性 + `register_func_ptr()` + fallback 解析 |
| `foundation/parsers/language_spec.py` | C/C++ spec 增加 func_ptr_assign query |
| `domains/core/graph/graph_updater.py` | 在 call processing 后调用新方法 |

## 5. Tree-sitter Query

### 点访问赋值

```scheme
(assignment_expression
  left: (field_expression) @lhs
  right: (identifier) @rhs) @assign
```

### 箭头访问赋值

C 的 `ptr->field = func;` 在 Tree-sitter 中解析为：

```scheme
(assignment_expression
  left: (field_expression
    argument: (pointer_expression) @ptr
    field: (field_identifier) @field)
  right: (identifier) @rhs) @assign
```

需验证实际 AST 结构，可能需要两个 query 合并或分别匹配。

## 6. 核心逻辑

### 6.1 CallProcessor.process_func_ptr_assignments()

```python
def process_func_ptr_assignments(
    self,
    file_path: Path,
    root_node: Node,
    language: SupportedLanguage,
    queries: dict[SupportedLanguage, LanguageQueries],
) -> None:
```

流程：

1. **Query 匹配** — 用 `QUERY_FUNC_PTR_ASSIGN` 查询捕获所有 `assignment_expression`
2. **提取 RHS** — 取 `identifier` 节点文本，作为候选函数名
3. **验证函数存在** — 在 `function_registry` 中查找 RHS，确认是已知函数定义。未找到则跳过。
4. **提取 LHS 字段名** — 从 `field_expression` 中取 `field_identifier` 文本（如 `on_error`）
5. **找到 enclosing function** — 复用 `_find_caller_function()` 找到赋值所在的函数
6. **注册映射** — 调用 `call_resolver.register_func_ptr(field_name, target_qn)`
7. **生成 CALLS 边** — `enclosing_func --CALLS--> target_func`，属性 `{"indirect": True, "via_field": field_name}`

### 6.2 CallResolver 扩展

新增属性和方法：

```python
# 属性
self._func_ptr_map: dict[str, str] = {}  # field_name -> target_qn

# 方法
def register_func_ptr(self, field_name: str, target_qn: str) -> None:
    """注册结构体字段 -> 函数的映射。"""
    self._func_ptr_map[field_name] = target_qn

def resolve_func_ptr_call(self, field_name: str) -> str | None:
    """通过字段名解析间接调用目标。"""
    return self._func_ptr_map.get(field_name)
```

在 `resolve_call()` 现有逻辑末尾增加 fallback：当常规解析失败且 call_name 形如 `obj.field` 时，提取 field 部分尝试 `resolve_func_ptr_call()`。

### 6.3 GraphUpdater 调用

在 `graph_updater.py` 的 call processing 循环中，对 C/C++ 文件追加调用：

```python
if language in (SupportedLanguage.C, SupportedLanguage.CPP):
    call_processor.process_func_ptr_assignments(
        file_path, root_node, language, queries
    )
```

## 7. CALLS 边属性

间接调用产生的 CALLS 边附加属性：

```python
properties = {
    "indirect": True,
    "via_field": "on_error",  # 结构体字段名
}
```

普通 CALLS 边不带这些属性（或 `indirect=False`）。

## 8. 边界情况

| 场景 | 处理 |
|------|------|
| RHS 不在 function_registry 中 | 跳过，不生成边 |
| 同一字段被赋值为不同函数 | 各自生成独立 CALLS 边 |
| 赋值在函数体外（全局初始化） | enclosing function 为 None，跳过 |
| 嵌套结构体 `a.b.field = func` | 只取最内层 field_identifier |
| 宏展开中的赋值 | 依赖 Tree-sitter 宏处理能力，best-effort |

## 9. 对 calltrace 模块的影响

**零改动** — 间接调用产生的 `CALLS` 边自动被 BFS 追踪。`wiki_writer` 后续可通过查询 `indirect` 属性自动填充 Indirect Call Paths 段落（不在本次范围）。

## 10. 测试策略

```
tests/foundation/parsers/test_func_ptr_detection.py
```

**测试用例：**

- 点访问赋值 `obj.field = func` — 验证 CALLS 边生成 + indirect 属性
- 箭头访问赋值 `ptr->field = func` — 同上
- RHS 非已知函数 — 不生成边
- 数组初始化 `{func_a, func_b}` — 不匹配
- 独立函数指针变量赋值 — 不匹配
- 赋值在全局作用域 — 跳过
- 同一字段多次赋值不同函数 — 各自独立边
- 间接调用点解析 `obj.field(args)` → 正确解析为 target

Mock `IngestorProtocol` 验证 `ensure_relationship_batch()` 的调用参数。使用 Tree-sitter 解析真实 C 代码片段。
