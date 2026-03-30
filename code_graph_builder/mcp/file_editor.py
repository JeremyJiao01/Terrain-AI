from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import diff_match_patch
from loguru import logger
from tree_sitter import Node, Parser

from .. import constants as cs
from ..language_spec import get_language_for_extension, get_language_spec
from ..parser_loader import load_parsers


class FileEditor:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()
        self._dmp = diff_match_patch.diff_match_patch()
        self._parsers, _ = load_parsers()
        logger.info(f"FileEditor initialised for: {self._repo_root}")

    def _get_real_extension(self, file_path: Path) -> str:
        ext = file_path.suffix
        if ext == ".tmp":
            stem = file_path.stem
            if "." in stem:
                return "." + stem.split(".")[-1]
        return ext

    def _get_parser(self, file_path: Path) -> Parser | None:
        ext = self._get_real_extension(file_path)
        lang = get_language_for_extension(ext)
        return self._parsers.get(lang) if lang else None

    def _extract_declarator_name(self, node: Node) -> str | None:
        if node.type == "identifier" and node.text:
            return node.text.decode(cs.ENCODING_UTF8)
        child = node.child_by_field_name("declarator")
        if child:
            return self._extract_declarator_name(child)
        return None

    def _extract_function_name(self, node: Node) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text:
            return name_node.text.decode(cs.ENCODING_UTF8)
        declarator = node.child_by_field_name("declarator")
        if declarator:
            return self._extract_declarator_name(declarator)
        return None

    def locate_function(
        self,
        file_path: Path,
        function_name: str,
        line_number: int | None = None,
    ) -> dict[str, Any] | None:
        parser = self._get_parser(file_path)
        if not parser:
            logger.warning(f"No parser for: {file_path}")
            return None

        try:
            content = file_path.read_bytes()
        except OSError as exc:
            logger.warning(f"Cannot read {file_path}: {exc}")
            return None

        tree = parser.parse(content)
        ext = self._get_real_extension(file_path)
        lang_config = get_language_spec(ext)
        if not lang_config:
            logger.warning(f"No language config for extension: {ext}")
            return None

        matching: list[dict[str, Any]] = []

        def traverse(node: Node, parent_class: str | None = None) -> None:
            if node.type in lang_config.function_node_types:
                func_name = self._extract_function_name(node)
                if func_name:
                    qualified = (
                        f"{parent_class}.{func_name}" if parent_class else func_name
                    )
                    if function_name in (func_name, qualified):
                        matching.append(
                            {
                                "node": node,
                                "simple_name": func_name,
                                "qualified_name": qualified,
                                "parent_class": parent_class,
                                "line_number": node.start_point[0] + 1,
                            }
                        )
                return

            current_class = parent_class
            if node.type in lang_config.class_node_types:
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text:
                    current_class = name_node.text.decode(cs.ENCODING_UTF8)

            for child in node.children:
                traverse(child, current_class)

        traverse(tree.root_node)

        if not matching:
            return None

        match_count = len(matching)

        def _build_result(m: dict[str, Any]) -> dict[str, Any] | None:
            node: Node = m["node"]
            if node.text is None:
                return None
            return {
                "qualified_name": m["qualified_name"],
                "source_code": node.text.decode(cs.ENCODING_UTF8),
                "start_line": m["line_number"],
                "end_line": node.end_point[0] + 1,
                "file_path": str(file_path),
                "match_count": match_count,
            }

        if match_count == 1:
            return _build_result(matching[0])

        if line_number is not None:
            for m in matching:
                if m["line_number"] == line_number:
                    return _build_result(m)
            logger.warning(
                f"'{function_name}' not found at line {line_number} in {file_path}"
            )
            return None

        if cs.SEPARATOR_DOT in function_name:
            for m in matching:
                if m["qualified_name"] == function_name:
                    return _build_result(m)
            logger.warning(
                f"'{function_name}' not found by qualified name in {file_path}"
            )
            return None

        details = ", ".join(
            f"'{m['qualified_name']}' at line {m['line_number']}" for m in matching
        )
        logger.warning(
            f"Ambiguous: '{function_name}' has {match_count} matches in {file_path}: "
            f"{details}. Returning first."
        )
        return _build_result(matching[0])

    def get_diff(
        self, original_code: str, new_code: str, label: str = "function"
    ) -> str:
        diff = difflib.unified_diff(
            original_code.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            fromfile=f"original/{label}",
            tofile=f"new/{label}",
        )
        return "".join(diff)

    def replace_code_block(
        self,
        file_path: Path,
        target_block: str,
        replacement_block: str,
    ) -> dict[str, Any]:
        try:
            if not file_path.is_file():
                return {"success": False, "error": f"File not found: {file_path}"}

            from ..utils.encoding import read_source_file
            original = read_source_file(file_path)

            if target_block not in original:
                return {"success": False, "error": "Target block not found in file."}

            multiple = original.count(target_block) > 1
            modified = original.replace(target_block, replacement_block, 1)

            if original == modified:
                return {
                    "success": False,
                    "error": "No changes: replacement is identical to target.",
                }

            patches = self._dmp.patch_make(original, modified)
            patched, results = self._dmp.patch_apply(patches, original)

            if not all(results):
                return {
                    "success": False,
                    "error": "patch_apply failed to apply all patches.",
                }

            file_path.write_text(patched, encoding=cs.ENCODING_UTF8)
            logger.success(f"Surgical replace succeeded: {file_path}")

            return {"success": True, "multiple_occurrences": multiple, "error": None}

        except Exception as exc:
            return {"success": False, "error": str(exc)}
