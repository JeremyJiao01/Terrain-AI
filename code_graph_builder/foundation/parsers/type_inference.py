"""Code Graph Builder - Type Inference."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tree_sitter import Node

from code_graph_builder.foundation.types import constants as cs
from .utils import safe_decode_text
from code_graph_builder.foundation.types.types import ASTCacheProtocol, FunctionRegistryTrieProtocol, SimpleNameLookup

if TYPE_CHECKING:
    from code_graph_builder.foundation.types.types import LanguageQueries
    from .import_processor import ImportProcessor


class TypeInferenceEngine:
    """Infer types from source code."""

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        repo_path: Path,
        project_name: str,
        ast_cache: ASTCacheProtocol,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        module_qn_to_file_path: dict[str, Path],
        class_inheritance: dict[str, list[str]],
        simple_name_lookup: SimpleNameLookup,
    ):
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.repo_path = repo_path
        self.project_name = project_name
        self.ast_cache = ast_cache
        self.queries = queries
        self.module_qn_to_file_path = module_qn_to_file_path
        self.class_inheritance = class_inheritance
        self.simple_name_lookup = simple_name_lookup
        self._variable_types: dict[str, dict[str, str]] = {}

    def infer_variable_type(
        self,
        var_name: str,
        scope_qn: str,
        local_node: Node | None = None,
    ) -> str | None:
        """Infer the type of a variable in a given scope."""
        # Check if we have cached type info
        if scope_qn in self._variable_types:
            if var_name in self._variable_types[scope_qn]:
                return self._variable_types[scope_qn][var_name]

        # Try to infer from local node
        if local_node:
            inferred = self._infer_from_node(var_name, local_node)
            if inferred:
                if scope_qn not in self._variable_types:
                    self._variable_types[scope_qn] = {}
                self._variable_types[scope_qn][var_name] = inferred
                return inferred

        return None

    def _infer_from_node(self, var_name: str, node: Node) -> str | None:
        """Try to infer type from AST node."""
        # Look for variable declaration
        for child in node.children:
            if child.type in (
                "variable_declarator",
                "variable_declaration",
                "lexical_declaration",
            ):
                type_hint = self._get_type_from_declaration(child, var_name)
                if type_hint:
                    return type_hint
        return None

    def _get_type_from_declaration(self, node: Node, var_name: str) -> str | None:
        """Extract type from a variable declaration."""
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node:
            name = safe_decode_text(name_node)
            if name == var_name:
                # Check for type annotation
                type_node = node.child_by_field_name(cs.FIELD_TYPE)
                if type_node:
                    return safe_decode_text(type_node)

                # Check for initialization value
                value_node = node.child_by_field_name(cs.FIELD_VALUE)
                if value_node:
                    return self._infer_from_value(value_node)

        return None

    def _infer_from_value(self, node: Node) -> str | None:
        """Infer type from a value node."""
        type_mapping = {
            "string": "str",
            "string_literal": "str",
            "integer": "int",
            "integer_literal": "int",
            "float": "float",
            "floating_point_literal": "float",
            "true": "bool",
            "false": "bool",
            "boolean_literal": "bool",
            "list": "list",
            "list_literal": "list",
            "dictionary": "dict",
            "dict_literal": "dict",
            "tuple": "tuple",
            "call_expression": None,  # Would need to resolve the call
        }

        return type_mapping.get(node.type)

    def get_class_for_variable(
        self,
        var_name: str,
        scope_qn: str,
        module_qn: str,
    ) -> str | None:
        """Get the class type for a variable."""
        var_type = self.infer_variable_type(var_name, scope_qn)
        if not var_type:
            return None

        # Check if it's a class from imports
        import_map = self.import_processor.get_import_mapping(module_qn)
        if var_type in import_map:
            return import_map[var_type]

        # Check if it's a local class
        class_qn = f"{module_qn}.{var_type}"
        if class_qn in self.class_inheritance:
            return class_qn

        return var_type
