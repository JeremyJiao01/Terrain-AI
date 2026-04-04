"""Layer dependency checker for the code_graph_builder 5-layer architecture.

Scans all Python files for import statements and checks them against layer rules:
  L0  foundation/types/         — stdlib + third-party only
  L1  foundation/{parsers,services,utils}/  — can import L0
  L2  domains/core/             — can import L0, L1 (no cross-domain)
  L3  domains/upper/            — can import L0, L1, L2 (no cross-domain)
  L4  entrypoints/              — can import L0, L1, L2, L3 (no cross-entrypoint)

Usage:
    python tools/dep_check.py [optional-root-path]
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

PKG = "code_graph_builder"

# Layer numeric order for comparison
LAYER_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

# Allowed import layers for each layer (does NOT include own layer)
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "L0": set(),           # no project imports at all
    "L1": {"L0"},
    "L2": {"L0", "L1"},
    "L3": {"L0", "L1", "L2"},
    "L4": {"L0", "L1", "L2", "L3"},
}


def classify_layer(file_path: str) -> str | None:
    """Determine the layer of a file from its path.

    Returns L0-L4 or None if the file is not in a recognized layer directory.
    """
    # Normalize to forward slashes for consistent matching
    parts = Path(file_path).parts

    # Find the package root
    try:
        pkg_idx = list(parts).index(PKG)
    except ValueError:
        return None

    # Get parts after the package name
    rel = parts[pkg_idx + 1:]
    if len(rel) < 2:
        return None

    if rel[0] == "foundation":
        if rel[1] == "types":
            return "L0"
        if rel[1] in ("parsers", "services", "utils"):
            return "L1"
    elif rel[0] == "domains":
        if len(rel) >= 3:
            if rel[1] == "core":
                return "L2"
            if rel[1] == "upper":
                return "L3"
    elif rel[0] == "entrypoints":
        return "L4"

    return None


def _get_domain(file_path: str) -> str | None:
    """Extract the domain/entrypoint name for cross-domain checks.

    For L2/L3: returns the subdomain name (e.g. 'graph', 'embedding', 'apidoc', 'rag')
    For L4: returns the entrypoint name (e.g. 'cli', 'mcp')
    """
    parts = Path(file_path).parts
    try:
        pkg_idx = list(parts).index(PKG)
    except ValueError:
        return None

    rel = parts[pkg_idx + 1:]

    # domains/core/<domain>/... or domains/upper/<domain>/...
    if len(rel) >= 3 and rel[0] == "domains" and rel[1] in ("core", "upper"):
        return rel[2]

    # entrypoints/<name>.py or entrypoints/<name>/...
    if rel[0] == "entrypoints":
        if len(rel) >= 2:
            # If the second part is a .py file, strip extension for domain name
            candidate = rel[1]
            if candidate.endswith(".py"):
                return candidate[:-3]
            return candidate
        return None

    return None


def _module_to_path_parts(module: str) -> list[str]:
    """Convert a dotted module name to path parts."""
    return module.split(".")


def _is_project_import(module: str) -> bool:
    """Check if a module is a project import (starts with package name)."""
    return module == PKG or module.startswith(PKG + ".")


def _classify_module(module: str) -> str | None:
    """Classify the layer of an imported module by its dotted name."""
    if not _is_project_import(module):
        return None

    parts = _module_to_path_parts(module)
    # parts[0] = 'code_graph_builder'
    if len(parts) < 3:
        return None

    if parts[1] == "foundation":
        if parts[2] == "types":
            return "L0"
        if parts[2] in ("parsers", "services", "utils"):
            return "L1"
    elif parts[1] == "domains":
        if len(parts) >= 4:
            if parts[2] == "core":
                return "L2"
            if parts[2] == "upper":
                return "L3"
    elif parts[1] == "entrypoints":
        return "L4"

    return None


def _get_module_domain(module: str) -> str | None:
    """Extract domain name from a dotted module import."""
    parts = _module_to_path_parts(module)
    if len(parts) < 4:
        # For entrypoints, domain might be parts[2]
        if len(parts) >= 3 and parts[1] == "entrypoints":
            return parts[2]
        return None

    if parts[1] == "domains" and parts[2] in ("core", "upper"):
        return parts[3]

    if parts[1] == "entrypoints":
        return parts[2]

    return None


def check_import(file_path: str, imported_module: str) -> str | None:
    """Check a single import against layer rules.

    Returns a violation message string, or None if the import is allowed.
    """
    file_layer = classify_layer(file_path)
    if file_layer is None:
        return None  # File not in a recognized layer; skip

    # Non-project imports (stdlib, third-party) are always allowed
    if not _is_project_import(imported_module):
        return None

    # L0 cannot have any project imports
    if file_layer == "L0":
        return (
            f"{file_path}: L0 cannot import project module '{imported_module}'"
        )

    imported_layer = _classify_module(imported_module)

    # If the imported module is in the project but not in a recognized layer,
    # allow it (could be a flat module not yet migrated)
    if imported_layer is None:
        return None

    # Same-layer imports: only allowed within the same domain (L2, L3, L4)
    if imported_layer == file_layer:
        if file_layer in ("L2", "L3", "L4"):
            file_domain = _get_domain(file_path)
            import_domain = _get_module_domain(imported_module)
            if (
                file_domain is not None
                and import_domain is not None
                and file_domain != import_domain
            ):
                return (
                    f"{file_path}: cross-domain import forbidden — "
                    f"'{file_domain}' cannot import from '{import_domain}' "
                    f"(module '{imported_module}')"
                )
            # Same domain within same layer is OK
            return None
        # L1 importing L1 is not allowed (no same-layer for L1)
        return (
            f"{file_path}: {file_layer} cannot import {imported_layer} "
            f"(module '{imported_module}')"
        )

    # Check layer ordering
    allowed = ALLOWED_IMPORTS[file_layer]
    if imported_layer not in allowed:
        return (
            f"{file_path}: {file_layer} cannot import {imported_layer} "
            f"(module '{imported_module}')"
        )

    return None


def scan_file(file_path: str, file_layer_path: str | None = None) -> list[str]:
    """Scan a Python file for all layer violations.

    Args:
        file_path: Actual path to the file on disk (for reading).
        file_layer_path: Logical path used for layer classification.
            If None, file_path is used.

    Returns:
        List of violation message strings.
    """
    layer_path = file_layer_path or file_path

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"WARNING: skipping {file_path}: {e}", file=sys.stderr)
        return []

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        print(f"WARNING: skipping {file_path}: {e}", file=sys.stderr)
        return []

    violations: list[str] = []

    for node in ast.walk(tree):
        modules: list[str] = []

        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)

        for mod in modules:
            violation = check_import(layer_path, mod)
            if violation is not None:
                violations.append(violation)

    return violations


def _should_skip(file_path: str) -> bool:
    """Check if a file should be skipped (tests/, examples/)."""
    parts = Path(file_path).parts
    return "tests" in parts or "examples" in parts


def main(root: str | None = None) -> int:
    """Entry point: scan all Python files under root for layer violations.

    Returns 0 if no violations, 1 otherwise.
    """
    if root is None:
        root = os.getcwd()

    root_path = Path(root)
    all_violations: list[str] = []

    for py_file in root_path.rglob("*.py"):
        rel = str(py_file.relative_to(root_path))

        if _should_skip(rel):
            continue

        layer = classify_layer(rel)
        if layer is None:
            continue  # Not in a recognized layer directory

        violations = scan_file(str(py_file), file_layer_path=rel)
        all_violations.extend(violations)

    if all_violations:
        for v in all_violations:
            print(f"VIOLATION: {v}")
        print(f"\n{len(all_violations)} violation(s) found.")
        return 1

    print("No layer violations found.")
    return 0


if __name__ == "__main__":
    root_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(root_arg))
