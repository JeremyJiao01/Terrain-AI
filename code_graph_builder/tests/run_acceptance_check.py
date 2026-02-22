#!/usr/bin/env python3
"""阶段二验收检查脚本.

执行所有验收检查项并生成报告。

用法:
    python run_acceptance_check.py [--tinycc-path PATH]

选项:
    --tinycc-path PATH  TinyCC 项目路径 [默认: /tmp/tinycc]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Colors:
    """终端颜色."""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"


def print_header(text: str) -> None:
    """打印标题."""
    print(f"\n{Colors.BLUE}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.RESET}\n")


def print_success(text: str) -> None:
    """打印成功信息."""
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")


def print_failure(text: str) -> None:
    """打印失败信息."""
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")


def print_warning(text: str) -> None:
    """打印警告信息."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")


def run_command(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """运行命令并返回结果."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


class AcceptanceChecker:
    """验收检查器."""

    def __init__(self, project_root: Path, tinycc_path: Path) -> None:
        self.project_root = project_root
        self.tinycc_path = tinycc_path
        self.results: dict[str, bool] = {}
        self.errors: dict[str, str] = {}

    def check_code_quality(self) -> None:
        """检查代码质量."""
        print_header("代码质量检查")

        # ruff check
        print("运行 ruff check...")
        returncode, stdout, stderr = run_command(
            ["uv", "run", "ruff", "check", "code_graph_builder/"],
            cwd=self.project_root,
        )
        if returncode == 0:
            print_success("ruff check 通过")
            self.results["ruff_check"] = True
        else:
            print_failure("ruff check 失败")
            self.errors["ruff_check"] = stderr or stdout
            self.results["ruff_check"] = False

        # ruff format check
        print("运行 ruff format --check...")
        returncode, stdout, stderr = run_command(
            ["uv", "run", "ruff", "format", "--check", "code_graph_builder/"],
            cwd=self.project_root,
        )
        if returncode == 0:
            print_success("ruff format 通过")
            self.results["ruff_format"] = True
        else:
            print_failure("ruff format 失败")
            self.errors["ruff_format"] = stderr or stdout
            self.results["ruff_format"] = False

        # ty type check
        print("运行 ty 类型检查...")
        returncode, stdout, stderr = run_command(
            ["uv", "run", "ty", "code_graph_builder/"],
            cwd=self.project_root,
        )
        if returncode == 0:
            print_success("ty 类型检查通过")
            self.results["ty_check"] = True
        else:
            print_failure("ty 类型检查失败")
            self.errors["ty_check"] = stderr or stdout
            self.results["ty_check"] = False

    def check_unit_tests(self) -> None:
        """检查单元测试."""
        print_header("单元测试检查")

        print("运行 code_graph_builder 单元测试...")
        returncode, stdout, stderr = run_command(
            [
                "uv",
                "run",
                "pytest",
                "code_graph_builder/tests/",
                "-v",
                "--tb=short",
            ],
            cwd=self.project_root,
        )

        if returncode == 0:
            print_success("所有单元测试通过")
            self.results["unit_tests"] = True
        else:
            print_failure("单元测试失败")
            self.errors["unit_tests"] = stderr or stdout
            self.results["unit_tests"] = False

    def check_test_coverage(self) -> None:
        """检查测试覆盖率."""
        print_header("测试覆盖率检查")

        print("运行覆盖率测试...")
        returncode, stdout, stderr = run_command(
            [
                "uv",
                "run",
                "pytest",
                "code_graph_builder/tests/",
                "--cov=code_graph_builder",
                "--cov-report=term-missing",
            ],
            cwd=self.project_root,
        )

        if returncode == 0:
            # 解析覆盖率
            for line in (stdout + stderr).split("\n"):
                if "TOTAL" in line and "%" in line:
                    parts = line.split()
                    for part in parts:
                        if "%" in part:
                            try:
                                coverage = float(part.replace("%", ""))
                                if coverage >= 80:
                                    print_success(f"测试覆盖率: {coverage}% (>= 80%)")
                                    self.results["test_coverage"] = True
                                else:
                                    print_failure(f"测试覆盖率: {coverage}% (< 80%)")
                                    self.results["test_coverage"] = False
                                return
                            except ValueError:
                                continue
            print_warning("无法解析覆盖率数据")
            self.results["test_coverage"] = False
        else:
            print_failure("覆盖率测试失败")
            self.errors["test_coverage"] = stderr or stdout
            self.results["test_coverage"] = False

    def check_tinycc_parsing(self) -> None:
        """检查 TinyCC 项目解析."""
        print_header("TinyCC 项目解析检查")

        if not self.tinycc_path.exists():
            print_warning(f"TinyCC 项目不存在: {self.tinycc_path}")
            print("跳过此检查")
            self.results["tinycc_parse"] = None
            return

        print(f"解析 TinyCC 项目: {self.tinycc_path}")
        start_time = time.time()

        try:
            # 动态导入避免依赖问题
            sys.path.insert(0, str(self.project_root))
            from code_graph_builder.builder import CodeGraphBuilder

            builder = CodeGraphBuilder(str(self.tinycc_path))
            result = builder.build_graph(clean=True)

            elapsed_time = time.time() - start_time

            print(f"解析时间: {elapsed_time:.2f} 秒")
            print(f"创建节点数: {result.nodes_created}")
            print(f"发现函数数: {result.functions_found}")
            print(f"创建关系数: {result.relationships_created}")

            # 性能检查
            if elapsed_time <= 5.0:
                print_success(f"解析时间达标: {elapsed_time:.2f}s <= 5s")
                self.results["parse_time"] = True
            else:
                print_failure(f"解析时间超标: {elapsed_time:.2f}s > 5s")
                self.results["parse_time"] = False

            # 功能检查 (TinyCC 大约有 1611 个函数)
            if result.functions_found >= 1400:
                print_success(f"函数识别达标: {result.functions_found} >= 1400")
                self.results["function_count"] = True
            else:
                print_failure(f"函数识别不足: {result.functions_found} < 1400")
                self.results["function_count"] = False

            # 关系检查
            if result.relationships_created > 0:
                print_success(f"关系创建成功: {result.relationships_created}")
                self.results["relationships"] = True
            else:
                print_failure("未创建任何关系")
                self.results["relationships"] = False

            self.results["tinycc_parse"] = all([
                self.results.get("parse_time", False),
                self.results.get("function_count", False),
                self.results.get("relationships", False),
            ])

        except Exception as e:
            print_failure(f"TinyCC 解析失败: {e}")
            self.errors["tinycc_parse"] = str(e)
            self.results["tinycc_parse"] = False

    def check_call_resolution_scenarios(self) -> None:
        """检查调用解析场景测试."""
        print_header("调用解析场景测试")

        print("运行调用解析场景测试...")
        returncode, stdout, stderr = run_command(
            [
                "uv",
                "run",
                "pytest",
                "code_graph_builder/tests/test_call_resolution_scenarios.py",
                "-v",
                "--tb=short",
            ],
            cwd=self.project_root,
        )

        if returncode == 0:
            print_success("调用解析场景测试通过")
            self.results["call_resolution"] = True
        else:
            print_failure("调用解析场景测试失败")
            self.errors["call_resolution"] = stderr or stdout
            self.results["call_resolution"] = False

    def generate_report(self) -> bool:
        """生成验收报告."""
        print_header("验收报告")

        # 必须通过的检查项
        required_checks = [
            "ruff_check",
            "ruff_format",
            "ty_check",
            "unit_tests",
            "call_resolution",
        ]

        # 可选但重要的检查项
        important_checks = [
            "test_coverage",
            "tinycc_parse",
        ]

        all_passed = True

        print("必须通过的检查项:")
        for check in required_checks:
            result = self.results.get(check)
            if result is True:
                print_success(f"  {check}")
            elif result is False:
                print_failure(f"  {check}")
                all_passed = False
            else:
                print_warning(f"  {check}: 未执行")
                all_passed = False

        print("\n重要检查项:")
        for check in important_checks:
            result = self.results.get(check)
            if result is True:
                print_success(f"  {check}")
            elif result is False:
                print_failure(f"  {check}")
            else:
                print_warning(f"  {check}: 未执行/跳过")

        print("\n" + "=" * 60)
        if all_passed:
            print_success("阶段二验收通过！")
        else:
            print_failure("阶段二验收未通过，请修复上述问题")
        print("=" * 60)

        # 显示错误详情
        if self.errors:
            print("\n错误详情:")
            for check, error in self.errors.items():
                print(f"\n{Colors.YELLOW}{check}:{Colors.RESET}")
                print(error[:500])  # 限制错误输出长度

        return all_passed


def main(argv: Sequence[str] | None = None) -> int:
    """主函数."""
    parser = argparse.ArgumentParser(description="阶段二验收检查")
    parser.add_argument(
        "--tinycc-path",
        type=Path,
        default=Path("/tmp/tinycc"),
        help="TinyCC 项目路径 (默认: /tmp/tinycc)",
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).parent.parent.parent

    print_header("Code Graph Builder 阶段二验收检查")
    print(f"项目根目录: {project_root}")
    print(f"TinyCC 路径: {args.tinycc_path}")

    checker = AcceptanceChecker(project_root, args.tinycc_path)

    # 执行所有检查
    checker.check_code_quality()
    checker.check_unit_tests()
    checker.check_test_coverage()
    checker.check_call_resolution_scenarios()
    checker.check_tinycc_parsing()

    # 生成报告
    passed = checker.generate_report()

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
