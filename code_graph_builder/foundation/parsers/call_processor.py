"""Code Graph Builder - Call Processor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, QueryCursor

from code_graph_builder.foundation.types import constants as cs
from .utils import safe_decode_text
from code_graph_builder.foundation.services import IngestorProtocol
from code_graph_builder.foundation.types.types import FunctionRegistryTrieProtocol

if TYPE_CHECKING:
    from code_graph_builder.foundation.types.types import LanguageQueries
    from .call_resolver import CallResolver
    from .import_processor import ImportProcessor
    from .type_inference import TypeInferenceEngine


class CallProcessor:
    """Process function calls in source code."""

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        type_inference: TypeInferenceEngine | None,
        class_inheritance: dict[str, list[str]],
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.import_processor = import_processor
        self.type_inference = type_inference
        self.class_inheritance = class_inheritance
        self._call_resolver: CallResolver | None = None

    def _get_call_resolver(self) -> CallResolver:
        """Get or create the call resolver."""
        if self._call_resolver is None:
            from .call_resolver import CallResolver

            self._call_resolver = CallResolver(
                function_registry=self.function_registry,
                import_processor=self.import_processor,
            )
        return self._call_resolver

    def process_calls_in_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Process all function calls in a file."""
        relative_path = file_path.relative_to(self.repo_path)
        logger.debug(f"Processing calls in: {relative_path}")

        try:
            lang_queries = queries.get(language)
            if not lang_queries:
                return

            call_query = lang_queries.get(cs.QUERY_CALLS)
            if not call_query:
                return

            # Build module qualified name
            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )

            # Process calls using the calls query
            cursor = QueryCursor(call_query)
            captures = cursor.captures(root_node)
            call_nodes = captures.get(cs.CAPTURE_CALL, [])

            for call_node in call_nodes:
                if not isinstance(call_node, Node):
                    continue

                self._process_call_node(
                    call_node, module_qn, language, root_node
                )

        except Exception as e:
            logger.warning(f"Failed to process calls in {file_path}: {e}")

    def process_func_ptr_assignments(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Detect struct field function pointer assignments and create indirect CALLS edges.

        Matches patterns like:
            config.on_error = handle_error;
            ptr->callback = process_data;

        Creates CALLS edges with indirect=True property.
        """
        relative_path = file_path.relative_to(self.repo_path)

        try:
            lang_queries = queries.get(language)
            if not lang_queries:
                return

            fp_query = lang_queries.get(cs.QUERY_FUNC_PTR_ASSIGN)
            if not fp_query:
                return

            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )

            cursor = QueryCursor(fp_query)
            captures = cursor.captures(root_node)

            assign_nodes = captures.get(cs.CAPTURE_ASSIGN, [])
            field_nodes = captures.get(cs.CAPTURE_FIELD, [])
            rhs_nodes = captures.get(cs.CAPTURE_RHS, [])

            for i, assign_node in enumerate(assign_nodes):
                if not isinstance(assign_node, Node):
                    continue
                if i >= len(field_nodes) or i >= len(rhs_nodes):
                    continue

                field_name = safe_decode_text(field_nodes[i])
                rhs_name = safe_decode_text(rhs_nodes[i])
                if not field_name or not rhs_name:
                    continue

                # Find enclosing function
                caller_qn = self._find_caller_function(assign_node, module_qn, language)
                if not caller_qn:
                    continue

                # Resolve RHS to a known function
                resolver = self._get_call_resolver()
                target_qn = resolver.resolve_call(rhs_name, module_qn, None)
                if not target_qn:
                    continue

                # Register in func_ptr_map for later call resolution
                resolver.register_func_ptr(field_name, target_qn)

                # Create indirect CALLS edge
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, caller_qn),
                    cs.RelationshipType.CALLS,
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, target_qn),
                    properties={"indirect": True, "via_field": field_name},
                )
                logger.debug(
                    f"Created indirect CALLS: {caller_qn} -> {target_qn} via .{field_name}"
                )

        except Exception as e:
            logger.warning(f"Failed to process func ptr assignments in {file_path}: {e}")

    def _process_call_node(
        self,
        call_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        root_node: Node,
    ) -> None:
        """Process a single call node."""
        # Extract the function name being called
        call_name = self._extract_call_name(call_node, language)
        if not call_name:
            return

        # Find the caller function (enclosing function)
        caller_qn = self._find_caller_function(call_node, module_qn, language)
        if not caller_qn:
            return

        # Resolve the target function
        class_context = self._get_class_context(call_node)
        resolver = self._get_call_resolver()
        target_qn = resolver.resolve_call(call_name, module_qn, class_context)

        if target_qn:
            # Create CALLS relationship
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, caller_qn),
                cs.RelationshipType.CALLS,
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, target_qn),
            )
            logger.debug(f"Created CALLS: {caller_qn} -> {target_qn}")

    def _extract_call_name(
        self, call_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        """Extract the function name from a call node."""
        # For call_expression, the function being called is typically in the "function" field
        func_node = call_node.child_by_field_name(cs.FIELD_NAME)
        if not func_node:
            # Try "function" field for call_expression
            func_node = call_node.child_by_field_name("function")

        if not func_node:
            return None

        # Handle different call patterns
        if func_node.type == "identifier":
            return safe_decode_text(func_node)
        elif func_node.type == "scoped_identifier":
            # For qualified calls like module.func()
            return self._get_scoped_name(func_node)
        elif func_node.type == "field_expression":
            # For method calls like obj.method()
            return self._get_field_expression_name(func_node)
        elif func_node.type == "member_expression":
            # JavaScript/TypeScript member access
            return self._get_member_expression_name(func_node)

        return safe_decode_text(func_node)

    def _get_scoped_name(self, node: Node) -> str | None:
        """Get the full name from a scoped identifier."""
        parts = []
        for child in node.children:
            if child.type == "identifier":
                name = safe_decode_text(child)
                if name:
                    parts.append(name)
        return ".".join(parts) if parts else None

    def _get_field_expression_name(self, node: Node) -> str | None:
        """Get name from a field expression (e.g., obj.method)."""
        # For C/C++ field expressions
        object_node = node.child_by_field_name(cs.FIELD_OBJECT)
        field_node = node.child_by_field_name(cs.FIELD_FIELD)

        if field_node:
            field_name = safe_decode_text(field_node)
            if object_node and field_name:
                obj_name = safe_decode_text(object_node)
                if obj_name:
                    return f"{obj_name}.{field_name}"
            return field_name

        return safe_decode_text(node)

    def _get_member_expression_name(self, node: Node) -> str | None:
        """Get name from a member expression (e.g., obj.method)."""
        object_node = node.child_by_field_name(cs.FIELD_OBJECT)
        property_node = node.child_by_field_name(cs.FIELD_PROPERTY)

        if property_node:
            prop_name = safe_decode_text(property_node)
            if object_node and prop_name:
                obj_name = safe_decode_text(object_node)
                if obj_name:
                    return f"{obj_name}.{prop_name}"
            return prop_name

        return safe_decode_text(node)

    def _find_caller_function(
        self,
        call_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
    ) -> str | None:
        """Find the enclosing function's qualified name."""
        current = call_node.parent

        while current:
            # Check if this is a function node
            if self._is_function_node(current, language):
                func_name = self._get_function_name(current, language)
                if func_name:
                    # Check if inside a class
                    class_name = self._get_enclosing_class_name(current)
                    if class_name:
                        return f"{module_qn}.{class_name}.{func_name}"
                    return f"{module_qn}.{func_name}"

            current = current.parent

        return None

    def _is_function_node(self, node: Node, language: cs.SupportedLanguage) -> bool:
        """Check if a node is a function definition."""
        func_types = {
            cs.SupportedLanguage.PYTHON: ("function_definition", "lambda"),
            cs.SupportedLanguage.JS: (
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
            ),
            cs.SupportedLanguage.TS: (
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
            ),
            cs.SupportedLanguage.C: ("function_definition",),
            cs.SupportedLanguage.CPP: ("function_definition", "lambda_expression"),
            cs.SupportedLanguage.JAVA: ("method_declaration", "constructor_declaration"),
            cs.SupportedLanguage.RUST: ("function_item", "closure_expression"),
            cs.SupportedLanguage.GO: ("function_declaration", "method_declaration"),
        }

        return node.type in func_types.get(language, ())

    def _get_function_name(
        self, func_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        """Get the name of a function node."""
        name_node = func_node.child_by_field_name(cs.FIELD_NAME)
        if name_node:
            return safe_decode_text(name_node)

        # For C/C++ function definitions with declarator
        declarator = func_node.child_by_field_name(cs.FIELD_DECLARATOR)
        if declarator:
            if declarator.type == "function_declarator":
                name_node = declarator.child_by_field_name(cs.FIELD_DECLARATOR)
                if name_node:
                    return safe_decode_text(name_node)
            else:
                return safe_decode_text(declarator)

        return None

    def _get_enclosing_class_name(self, node: Node) -> str | None:
        """Get the name of the enclosing class if any."""
        current = node.parent

        while current:
            if current.type in (
                "class_definition",
                "class_declaration",
                "class_specifier",
                "struct_specifier",
                "impl_item",
            ):
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node:
                    return safe_decode_text(name_node)
            current = current.parent

        return None

    def _get_class_context(self, node: Node) -> str | None:
        """Get the class context for a node (for self/this calls)."""
        current = node.parent

        while current:
            if current.type in (
                "class_definition",
                "class_declaration",
                "class_specifier",
                "struct_specifier",
            ):
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node:
                    return safe_decode_text(name_node)
            current = current.parent

        return None
