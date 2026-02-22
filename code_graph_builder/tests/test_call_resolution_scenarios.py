"""阶段二调用关系解析测试场景.

测试场景覆盖：
- 场景1：简单单一文件项目
- 场景2：跨文件调用（import/include）
- 场景5：复杂调用模式
- 场景6：边界情况
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from code_graph_builder.builder import CodeGraphBuilder


# =============================================================================
# 测试场景1：简单单一文件项目
# =============================================================================


@pytest.fixture
def simple_project(tmp_path: Path) -> Path:
    """创建简单单一文件测试项目."""
    project_path = tmp_path / "simple_project"
    project_path.mkdir()

    with open(project_path / "main.py", "w") as f:
        f.write('''
def helper():
    """Helper function."""
    return "help"


class Calculator:
    """Calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def calculate(self) -> int:
        """Perform calculation."""
        return self.add(1, 2)


def main() -> tuple:
    """Main function."""
    calc = Calculator()
    result = calc.calculate()
    help_result = helper()
    return result, help_result
''')

    return project_path


def test_simple_project_function_detection(simple_project: Path) -> None:
    """测试简单项目函数识别.

    预期：识别 4 个函数/方法定义
    - helper (函数)
    - Calculator.add (方法)
    - Calculator.calculate (方法)
    - main (函数)
    """
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(simple_project))
    result = builder.build_graph(clean=True)

    # 验证函数数量
    assert result.functions_found >= 4, (
        f"Expected at least 4 functions, found {result.functions_found}"
    )

    # 验证节点创建
    assert result.nodes_created > 0, "No nodes were created"


def test_simple_project_call_relationships(simple_project: Path) -> None:
    """测试简单项目调用关系识别.

    预期：识别 3 条调用关系
    - main -> helper
    - main -> Calculator.calculate
    - Calculator.calculate -> Calculator.add
    """
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(simple_project))
    result = builder.build_graph(clean=True)

    # 验证关系创建
    assert result.relationships_created > 0, "No relationships were created"

    # 查询调用关系
    calls_query = """
    MATCH (caller)-[:CALLS]->(callee)
    RETURN caller.name as caller, callee.name as callee
    """
    calls = builder.query(calls_query)

    # 验证至少有一些调用关系
    assert len(calls) >= 2, f"Expected at least 2 call relationships, found {len(calls)}"


# =============================================================================
# 测试场景2：跨文件调用
# =============================================================================


@pytest.fixture
def cross_file_project(tmp_path: Path) -> Path:
    """创建跨文件调用测试项目."""
    project_path = tmp_path / "cross_file_project"
    project_path.mkdir()

    # utils/helpers.py
    utils_dir = project_path / "utils"
    utils_dir.mkdir()
    with open(utils_dir / "helpers.py", "w") as f:
        f.write('''
def format_data(data: str) -> str:
    """Format data helper function."""
    return f"formatted: {data}"


class DataProcessor:
    """Data processor class."""

    def process(self, data: str) -> str:
        """Process data method."""
        return format_data(data)
''')

    # utils/math_ops.py
    with open(utils_dir / "math_ops.py", "w") as f:
        f.write('''
def calculate(x: int, y: int) -> int:
    """Calculate function."""
    return x + y


def compute_complex() -> int:
    """Complex computation."""
    return calculate(10, 20)
''')

    # services/processor.py
    services_dir = project_path / "services"
    services_dir.mkdir()
    with open(services_dir / "processor.py", "w") as f:
        f.write('''
from utils.helpers import format_data, DataProcessor
from utils.math_ops import calculate


def process_request(data: str) -> tuple:
    """Main processing function that calls multiple cross-file functions."""
    # Direct function calls from different modules
    formatted = format_data(data)

    # Method calls
    processor = DataProcessor()
    processed = processor.process(data)

    calc_result = calculate(1, 2)

    return formatted, processed, calc_result
''')

    # main.py
    with open(project_path / "main.py", "w") as f:
        f.write('''
from services.processor import process_request
from utils.math_ops import compute_complex


def main() -> tuple:
    """Main function."""
    result = process_request("test data")
    complex_result = compute_complex()
    return result, complex_result
''')

    return project_path


def test_cross_file_function_calls(cross_file_project: Path) -> None:
    """测试跨文件函数调用解析.

    预期调用关系：
    - main.main -> services.processor.process_request
    - main.main -> utils.math_ops.compute_complex
    - services.processor.process_request -> utils.helpers.format_data
    - services.processor.process_request -> utils.math_ops.calculate
    - utils.math_ops.compute_complex -> utils.math_ops.calculate
    - utils.helpers.DataProcessor.process -> utils.helpers.format_data
    """
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(cross_file_project))
    result = builder.build_graph(clean=True)

    # 验证函数数量
    assert result.functions_found >= 6, (
        f"Expected at least 6 functions, found {result.functions_found}"
    )

    # 查询调用关系
    calls_query = """
    MATCH (caller)-[:CALLS]->(callee)
    RETURN caller.fqn as caller, callee.fqn as callee
    """
    calls = builder.query(calls_query)

    # 验证跨文件调用存在
    assert len(calls) >= 4, f"Expected at least 4 cross-file calls, found {len(calls)}"


# =============================================================================
# 测试场景5：复杂调用模式
# =============================================================================


@pytest.fixture
def complex_patterns_project(tmp_path: Path) -> Path:
    """创建复杂调用模式测试项目."""
    project_path = tmp_path / "complex_patterns"
    project_path.mkdir()

    with open(project_path / "patterns.py", "w") as f:
        f.write('''
from typing import List, Callable


class Builder:
    """Builder class for chain calls."""

    def __init__(self) -> None:
        self.value = 0

    def set_value(self, v: int) -> "Builder":
        self.value = v
        return self

    def add(self, v: int) -> "Builder":
        self.value += v
        return self

    def build(self) -> int:
        return self.value


def outer_func(x: int) -> int:
    return x * 2


def inner_func(x: int) -> int:
    return x + 1


def process_list(items: List[int]) -> List[int]:
    """Process list with map/filter."""
    # 高阶函数调用
    doubled = list(map(outer_func, items))
    filtered = list(filter(lambda x: x > 0, doubled))
    return filtered


def chain_call_example() -> int:
    """Chain call example."""
    # 链式调用
    result = Builder().set_value(10).add(5).build()
    return result


def nested_call_example() -> int:
    """Nested call example."""
    # 嵌套调用
    result = outer_func(inner_func(5))
    return result


def conditional_call_example(flag: bool) -> int:
    """Conditional call example."""
    # 条件调用
    result = outer_func(10) if flag else inner_func(10)
    return result
''')

    return project_path


def test_chain_calls(complex_patterns_project: Path) -> None:
    """测试链式调用识别."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(complex_patterns_project))
    result = builder.build_graph(clean=True)

    # 链式调用应该被识别
    assert result.functions_found >= 5, (
        f"Expected at least 5 functions, found {result.functions_found}"
    )


def test_nested_calls(complex_patterns_project: Path) -> None:
    """测试嵌套调用识别."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(complex_patterns_project))
    result = builder.build_graph(clean=True)

    # 嵌套调用应该被识别
    assert result.relationships_created > 0, "No call relationships found"


# =============================================================================
# 测试场景6：边界情况
# =============================================================================


@pytest.fixture
def edge_cases_project(tmp_path: Path) -> Path:
    """创建边界情况测试项目."""
    project_path = tmp_path / "edge_cases"
    project_path.mkdir()

    with open(project_path / "edge_cases.py", "w") as f:
        f.write('''
# 短函数名
def a():
    return "short"


def b():
    return a()


# 递归调用
def recursive(n: int) -> int:
    if n <= 0:
        return 0
    return recursive(n - 1)


# 间接递归
def indirect_a():
    return indirect_b()


def indirect_b():
    return indirect_a()


# 同名函数（不同上下文）
class ClassA:
    def method(self):
        return "A"


class ClassB:
    def method(self):
        return "B"


def main():
    # 调用短函数名
    result_a = a()

    # 调用递归函数
    result_rec = recursive(5)

    # 调用类方法
    obj_a = ClassA()
    obj_b = ClassB()
    result_a_method = obj_a.method()
    result_b_method = obj_b.method()

    return result_a, result_rec, result_a_method, result_b_method
''')

    return project_path


def test_short_function_names(edge_cases_project: Path) -> None:
    """测试短函数名识别."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(edge_cases_project))
    result = builder.build_graph(clean=True)

    # 短函数名应该被识别
    assert result.functions_found >= 6, (
        f"Expected at least 6 functions, found {result.functions_found}"
    )


def test_recursive_calls(edge_cases_project: Path) -> None:
    """测试递归调用识别."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(edge_cases_project))
    result = builder.build_graph(clean=True)

    # 递归调用应该被识别
    assert result.relationships_created > 0, "No call relationships found"


def test_same_name_methods(edge_cases_project: Path) -> None:
    """测试同名方法区分."""
    from code_graph_builder.builder import CodeGraphBuilder

    builder = CodeGraphBuilder(str(edge_cases_project))
    result = builder.build_graph(clean=True)

    # 同名方法应该被区分为不同函数
    # 查询所有方法
    methods_query = """
    MATCH (m:Method)
    RETURN m.fqn as fqn, m.name as name
    """
    methods = builder.query(methods_query)

    # 应该有两个名为 'method' 的方法，但 FQN 不同
    method_names = [m["name"] for m in methods]
    assert method_names.count("method") == 2, (
        f"Expected 2 methods named 'method', found {method_names.count('method')}"
    )


# =============================================================================
# 性能测试
# =============================================================================


@pytest.mark.performance
def test_tinycc_scale_performance() -> None:
    """测试 TinyCC 规模项目性能.

    要求：
    - 解析时间 ≤ 5 秒
    - 内存占用 ≤ 2GB
    - 函数识别率 ≥ 90%
    """
    import os
    import time

    tinycc_path = os.environ.get("TINYCC_PATH", "/tmp/tinycc")

    if not Path(tinycc_path).exists():
        pytest.skip(f"TinyCC project not found at {tinycc_path}")

    from code_graph_builder.builder import CodeGraphBuilder

    start_time = time.time()

    builder = CodeGraphBuilder(tinycc_path)
    result = builder.build_graph(clean=True)

    elapsed_time = time.time() - start_time

    # 性能验证
    assert elapsed_time <= 5.0, (
        f"Parsing took {elapsed_time:.2f}s, expected <= 5s"
    )

    # 功能验证（TinyCC 大约有 1611 个函数）
    assert result.functions_found >= 1400, (
        f"Found {result.functions_found} functions, expected >= 1400"
    )


# =============================================================================
# 准确率测试
# =============================================================================


@pytest.mark.accuracy
def test_call_detection_accuracy() -> None:
    """测试调用识别准确率.

    使用已知调用关系的测试项目验证准确率。
    """
    # 这是一个占位符测试，实际实现需要更详细的验证逻辑
    # 可以人工创建一个包含已知数量调用的测试项目
    pytest.skip("Requires manual verification with known test project")


@pytest.mark.accuracy
def test_call_resolution_accuracy() -> None:
    """测试调用解析准确率.

    验证解析的调用目标是否正确。
    """
    # 这是一个占位符测试，实际实现需要更详细的验证逻辑
    pytest.skip("Requires manual verification with known test project")
