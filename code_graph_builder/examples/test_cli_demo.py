#!/usr/bin/env python3
"""Demonstration of Code Graph Builder CLI.

This script shows all CLI commands without actually running them.
For actual usage, run the commands directly in your terminal.
"""

import subprocess
import sys


def run_command(cmd: str, description: str) -> None:
    """Print command description and the command itself."""
    print("=" * 80)
    print(f"{description}")
    print("=" * 80)
    print(f"$ {cmd}")
    print()


def main():
    """Show CLI examples."""
    repo_path = "/Users/jiaojeremy/CodeFile/tinycc"
    db_path = "/tmp/demo_graph.db"

    print("Code Graph Builder - CLI 演示")
    print()
    print("注意: 以下只是命令示例，不会实际执行")
    print("在实际终端中运行这些命令来体验完整功能")
    print()

    # 1. Help
    run_command(
        "code-graph-builder --help",
        "1. 查看帮助信息"
    )

    # 2. Scan
    run_command(
        f"code-graph-builder scan {repo_path} --db-path {db_path} --clean",
        "2. 扫描代码仓库"
    )

    run_command(
        f"code-graph-builder scan {repo_path} \\\n"
        f"  --db-path {db_path} \\\n"
        f"  --exclude tests,win32,examples \\\n"
        f"  --language c \\\n"
        f"  --clean",
        "3. 扫描（带过滤选项）"
    )

    # 3. Query
    run_command(
        f"code-graph-builder query \\\n"
        f'  "MATCH (f:Function) RETURN f.name LIMIT 10" \\\n'
        f"  --db-path {db_path}",
        "4. 查询函数"
    )

    run_command(
        f"code-graph-builder query \\\n"
        f'  "MATCH (caller:Function)-[:CALLS]->(callee:Function) \\\n'
        f'   WHERE callee.name = \\\'parse_expr\\\' \\\n'
        f'   RETURN caller.name" \\\n'
        f"  --db-path {db_path}",
        "5. 查询调用关系"
    )

    # 4. Stats
    run_command(
        f"code-graph-builder stats --db-path {db_path}",
        "6. 查看统计信息"
    )

    # 5. Export
    run_command(
        f"code-graph-builder export {repo_path} \\\n"
        f"  --output /tmp/graph.json \\\n"
        f"  --build \\\n"
        f"  --exclude tests",
        "7. 导出为 JSON"
    )

    # 6. Using config file
    run_command(
        f"code-graph-builder scan {repo_path} \\\n"
        f"  --config code-graph-builder.example.yaml",
        "8. 使用配置文件"
    )

    print("=" * 80)
    print("实际运行测试")
    print("=" * 80)
    print()

    # Actually run a quick test
    print("运行: code-graph-builder --version")
    result = subprocess.run(
        [sys.executable, "-m", "code_graph_builder.cli", "--version"],
        capture_output=True,
        text=True
    )
    print(result.stdout or result.stderr)

    print("运行: code-graph-builder stats --help")
    result = subprocess.run(
        [sys.executable, "-m", "code_graph_builder.cli", "stats", "--help"],
        capture_output=True,
        text=True
    )
    print(result.stdout)
    print()

    print("=" * 80)
    print("CLI 演示完成!")
    print("=" * 80)
    print()
    print("快速参考:")
    print("  code-graph-builder scan <repo> --db-path <path>  # 扫描代码")
    print("  code-graph-builder query '<cypher>'               # 查询")
    print("  code-graph-builder stats                          # 统计")
    print("  code-graph-builder export <repo> -o <file>        # 导出")
    print()
    print("详细文档: CLI.md")


if __name__ == "__main__":
    main()
