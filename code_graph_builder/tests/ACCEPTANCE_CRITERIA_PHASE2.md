# 阶段二完成验收标准与测试方案

## 1. 概述

本文档定义 code_graph_builder 项目阶段二（完善核心解析逻辑，特别是调用关系解析）的完成标准和测试方案。

**阶段二核心目标**：
- 完善调用关系解析逻辑
- 支持多种调用类型的准确识别
- 确保跨文件调用解析正确
- 达到可接受的准确率和性能指标

---

## 2. 调用关系解析完成标准

### 2.1 必须识别的调用类型

| 调用类型 | 说明 | 示例 | 优先级 |
|---------|------|------|--------|
| **直接函数调用** | 同文件内的函数调用 | `foo()` | P0 |
| **跨文件函数调用** | 通过 import/include 的调用 | `from utils import foo` → `foo()` | P0 |
| **方法调用** | 对象方法调用 | `obj.method()` | P0 |
| **链式调用** | 链式方法调用 | `obj.a().b()` | P1 |
| **静态方法调用** | 类/模块静态方法 | `Class.static_method()` | P1 |
| **父类方法调用** | super/父类方法调用 | `super().method()` | P1 |
| **IIFE 调用** | 立即执行函数 | `(function(){})()` | P2 |
| **回调/高阶函数** | 函数作为参数传递 | `map(fn, list)` | P2 |
| **构造函数调用** | 对象实例化 | `new Class()` / `Class()` | P1 |

### 2.2 准确率要求

| 指标 | 目标值 | 最低可接受值 | 测量方法 |
|------|--------|-------------|---------|
| **调用识别率** | ≥ 95% | ≥ 90% | 实际调用数 / 应识别调用数 |
| **调用解析准确率** | ≥ 90% | ≥ 85% | 正确解析的调用 / 总解析调用 |
| **跨文件调用准确率** | ≥ 85% | ≥ 80% | 正确解析的跨文件调用 / 总跨文件调用 |
| **误报率** | ≤ 5% | ≤ 10% | 错误识别的调用 / 总识别调用 |

### 2.3 性能要求

| 指标 | 目标值 | 最高可接受值 | 测试条件 |
|------|--------|-------------|---------|
| **解析速度** | ≥ 1000 函数/秒 | ≥ 500 函数/秒 | TinyCC 规模项目（1611 函数） |
| **内存占用** | ≤ 2GB | ≤ 4GB | 解析 TinyCC 项目峰值内存 |
| **数据库写入** | ≥ 500 节点/秒 | ≥ 200 节点/秒 | 批量写入模式 |

---

## 3. 测试场景设计

### 3.1 测试场景矩阵

```
                    简单项目    中型项目    大型项目
                    (1-10文件)  (10-100)   (100+)
─────────────────────────────────────────────────────
单语言 (Python)     [场景1]    [场景4]    [场景7]
双语言 (Py+JS)      [场景2]    [场景5]    [场景8]
多语言 (3+语言)     [场景3]    [场景6]    [场景9]
```

### 3.2 详细测试场景

#### 场景1：简单单一文件项目
**目的**：验证基本调用识别能力

**测试代码示例**：
```python
# simple_project/main.py
def helper():
    return "help"

class Calculator:
    def add(self, a, b):
        return a + b

    def calculate(self):
        return self.add(1, 2)

def main():
    calc = Calculator()
    result = calc.calculate()
    help_result = helper()
    return result, help_result
```

**预期结果**：
- 识别 4 个函数/方法定义
- 识别 3 条调用关系：
  - `main` → `helper`
  - `main` → `Calculator.calculate`
  - `Calculator.calculate` → `Calculator.add`

**通过标准**：
- [ ] 所有函数被正确识别
- [ ] 所有调用关系被正确识别
- [ ] 无错误解析

---

#### 场景2：跨文件调用（Python）
**目的**：验证 import 解析和跨文件调用识别

**项目结构**：
```
cross_file_project/
├── utils/
│   ├── __init__.py
│   ├── helpers.py
│   └── math_ops.py
├── services/
│   ├── __init__.py
│   └── processor.py
└── main.py
```

**测试代码示例**：
```python
# utils/helpers.py
def format_data(data):
    return f"formatted: {data}"

class DataProcessor:
    def process(self, data):
        return format_data(data)

# utils/math_ops.py
def calculate(x, y):
    return x + y

# services/processor.py
from utils.helpers import format_data, DataProcessor
from utils.math_ops import calculate

def process_request(data):
    formatted = format_data(data)
    processor = DataProcessor()
    processed = processor.process(data)
    calc_result = calculate(1, 2)
    return formatted, processed, calc_result

# main.py
from services.processor import process_request

def main():
    return process_request("test")
```

**预期调用关系**：
| 调用者 | 被调用者 | 类型 |
|-------|---------|------|
| `main.main` | `services.processor.process_request` | 跨模块函数 |
| `services.processor.process_request` | `utils.helpers.format_data` | 跨模块函数 |
| `services.processor.process_request` | `utils.math_ops.calculate` | 跨模块函数 |
| `services.processor.process_request` | `utils.helpers.DataProcessor.process` | 跨模块方法 |
| `utils.helpers.DataProcessor.process` | `utils.helpers.format_data` | 同模块函数 |

**通过标准**：
- [ ] 所有 5 条调用关系被正确识别
- [ ] import 语句被正确解析
- [ ] FQN（完全限定名）构建正确

---

#### 场景3：多语言混合项目
**目的**：验证多语言支持能力

**项目结构**：
```
multi_lang_project/
├── python_api/
│   ├── __init__.py
│   └── api.py
├── js_frontend/
│   └── app.js
└── rust_core/
    └── lib.rs
```

**测试重点**：
- Python 调用 Python（已验证）
- JavaScript 函数调用识别
- Rust 函数调用识别
- 每种语言的调用解析独立正确

**通过标准**：
- [ ] Python 调用识别率 ≥ 90%
- [ ] JavaScript 调用识别率 ≥ 85%
- [ ] Rust 调用识别率 ≥ 80%

---

#### 场景4：中型项目（TinyCC 规模）
**目的**：验证实际项目解析能力

**测试对象**：TinyCC 项目（已验证：43 文件，1611 函数）

**测试内容**：
1. **完整性检查**
   - [ ] 所有文件被解析
   - [ ] 所有函数被识别
   - [ ] 无解析错误导致程序退出

2. **调用关系检查**（抽样）
   - [ ] 随机抽取 20 个函数，验证其调用关系
   - [ ] 验证主要调用链完整

3. **性能检查**
   - [ ] 解析时间 ≤ 5 秒
   - [ ] 内存占用 ≤ 2GB

---

#### 场景5：复杂调用模式
**目的**：验证复杂调用模式的识别

**测试代码示例**：
```python
# 链式调用
result = obj.a().b().c()

# 嵌套调用
result = outer(inner(data))

# 高阶函数
results = map(process, items)
filtered = filter(lambda x: x > 0, data)

# 条件调用
result = obj.method() if condition else other_method()

# 动态调用（可选，可能无法完全支持）
method = getattr(obj, method_name)
method()
```

**通过标准**：
- [ ] 链式调用识别率 ≥ 80%
- [ ] 嵌套调用识别率 ≥ 90%
- [ ] 高阶函数调用识别率 ≥ 70%

---

#### 场景6：边界情况测试
**目的**：验证边界情况的处理

**测试用例**：

| 用例 | 代码示例 | 预期行为 |
|------|---------|---------|
| 短函数名 | `def a(): pass` → `a()` | 正确识别 |
| 同名函数 | 不同模块的同名函数 | 正确区分 FQN |
| 递归调用 | `def f(): f()` | 正确识别自调用 |
| 间接递归 | `def a(): b()` / `def b(): a()` | 正确识别循环调用 |
| 未定义函数 | `undefined_func()` | 记录但不报错 |
| 内置函数 | `print()`, `len()` | 可选识别 |
| 第三方库 | `import numpy` → `numpy.array()` | 记录但不解析 |

---

## 4. 验收检查清单

### 4.1 功能检查清单

#### 解析功能
- [ ] 单文件项目解析通过（场景1）
- [ ] 跨文件调用解析通过（场景2）
- [ ] 多语言项目解析通过（场景3）
- [ ] TinyCC 项目完整解析通过（场景4）
- [ ] 复杂调用模式识别通过（场景5）
- [ ] 边界情况处理通过（场景6）

#### 调用类型支持
- [ ] 直接函数调用识别
- [ ] 方法调用识别
- [ ] 跨文件函数调用解析
- [ ] 链式调用识别
- [ ] 静态方法调用识别
- [ ] 构造函数调用识别

#### 数据正确性
- [ ] 节点数量与实际代码一致
- [ ] 关系数量与预期一致
- [ ] FQN 格式正确
- [ ] 行号信息准确

### 4.2 性能检查清单

- [ ] TinyCC 项目解析时间 ≤ 5 秒
- [ ] TinyCC 项目内存占用 ≤ 2GB
- [ ] 数据库批量写入正常
- [ ] 无内存泄漏（连续解析 3 次内存稳定）

### 4.3 代码质量检查清单

- [ ] 所有单元测试通过
- [ ] 代码覆盖率 ≥ 80%
- [ ] 类型检查通过（ty）
- [ ] 代码风格检查通过（ruff）
- [ ] 无 `Any` 类型使用
- [ ] 无 `cast()` 使用

---

## 5. "阶段二完成"定义

### 5.1 必须完成项（阻塞项）

以下所有项必须完成，阶段二才算完成：

| 序号 | 完成项 | 验证方法 |
|------|--------|---------|
| 1 | 调用处理器完整移植 | 代码审查 |
| 2 | 调用解析器完整移植 | 代码审查 |
| 3 | 类型推断引擎完整移植 | 代码审查 |
| 4 | 导入处理器完整移植 | 代码审查 |
| 5 | 场景1测试通过 | 执行测试 |
| 6 | 场景2测试通过 | 执行测试 |
| 7 | TinyCC 项目解析通过 | 执行测试 |
| 8 | 调用识别率 ≥ 90% | 测试报告 |
| 9 | 调用解析准确率 ≥ 85% | 测试报告 |
| 10 | 单元测试覆盖率 ≥ 80% | 覆盖率报告 |

### 5.2 建议完成项（非阻塞）

以下项建议完成，但不阻塞阶段二完成：

- [ ] 多语言混合项目测试（场景3）
- [ ] 复杂调用模式测试（场景5）
- [ ] 边界情况全面测试（场景6）
- [ ] 性能优化达到目标值
- [ ] 完整文档编写

### 5.3 阶段二完成签字

| 角色 | 签字 | 日期 |
|------|------|------|
| 技术负责人 | | |
| 质量保障 | | |
| 产品经理 | | |

---

## 6. 测试执行命令

```bash
# 1. 运行所有单元测试
cd /Users/jiaojeremy/CodeFile/code-graph-rag
uv run pytest code_graph_builder/tests/ -v

# 2. 运行特定测试场景
uv run pytest code_graph_builder/tests/test_call_processor.py -v
uv run pytest code_graph_builder/tests/test_call_resolver.py -v

# 3. 运行覆盖率测试
uv run pytest code_graph_builder/tests/ --cov=code_graph_builder --cov-report=html

# 4. 代码质量检查
uv run ruff check code_graph_builder/
uv run ruff format --check code_graph_builder/
uv run ty code_graph_builder/

# 5. TinyCC 项目解析测试
python -c "
from code_graph_builder import CodeGraphBuilder
builder = CodeGraphBuilder('/path/to/tinycc')
result = builder.build_graph(clean=True)
print(f'Nodes: {result.nodes_created}')
print(f'Functions: {result.functions_found}')
print(f'Relationships: {result.relationships_created}')
"
```

---

## 7. 附录

### 7.1 术语定义

| 术语 | 定义 |
|------|------|
| FQN | Fully Qualified Name，完全限定名，如 `project.module.Class.method` |
| IIFE | Immediately Invoked Function Expression，立即执行函数表达式 |
| CALLS 关系 | 图中表示函数调用关系的关系类型 |
| 调用识别率 | 识别出的调用数 / 实际存在的调用数 |
| 调用解析准确率 | 正确解析目标函数的调用 / 总解析调用数 |

### 7.2 参考文件

- `/Users/jiaojeremy/CodeFile/code-graph-rag/PORTING_TASKS.md` - 移植任务总览
- `/Users/jiaojeremy/CodeFile/code-graph-rag/code_graph_builder/PORTING_CHANGES.md` - 移植变更记录
- `/Users/jiaojeremy/CodeFile/code-graph-rag/codebase_rag/tests/test_call_processor.py` - 原项目调用处理器测试
- `/Users/jiaojeremy/CodeFile/code-graph-rag/codebase_rag/tests/test_call_resolver.py` - 原项目调用解析器测试
- `/Users/jiaojeremy/CodeFile/code-graph-rag/codebase_rag/tests/test_complex_cross_file_calls.py` - 原项目跨文件调用测试

---

*文档版本：1.0*
*创建日期：2026-02-21*
*适用阶段：阶段二验收*
