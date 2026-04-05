"""Definition processor for ingesting code definitions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, QueryCursor

from code_graph_builder.foundation.types import constants as cs
from code_graph_builder.foundation.services import IngestorProtocol
from code_graph_builder.foundation.types.types import LanguageQueries, NodeType, PropertyDict, SimpleNameLookup
from code_graph_builder.foundation.utils.encoding import normalize_to_utf8_bytes
from .utils import safe_decode_text

if TYPE_CHECKING:
    from code_graph_builder.foundation.types.types import FunctionRegistryTrieProtocol
    from .import_processor import ImportProcessor


class DefinitionProcessor:
    """Process file definitions (functions, classes, methods)."""

    # C language storage class specifiers that indicate static (file-local) visibility
    _C_STATIC_SPECIFIER = "storage_class_specifier"

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        simple_name_lookup: SimpleNameLookup,
        import_processor: ImportProcessor,
        module_qn_to_file_path: dict[str, Path],
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.class_inheritance: dict[str, list[str]] = {}
        # Track function declarations found in header files for visibility resolution
        self._header_declarations: set[str] = set()

    def process_file(
        self,
        file_path: Path,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        structural_elements: dict[Path, str | None],
    ) -> tuple[Node, cs.SupportedLanguage] | None:
        """Process a single file and extract definitions."""
        relative_path = file_path.relative_to(self.repo_path)
        logger.info(f"Processing file: {relative_path}")

        try:
            lang_queries = queries.get(language)
            if not lang_queries:
                logger.warning(f"No queries for language: {language}")
                return None

            parser = lang_queries.get("parser")
            if not parser:
                logger.warning(f"No parser for language: {language}")
                return None

            source_bytes = normalize_to_utf8_bytes(file_path.read_bytes())
            tree = parser.parse(source_bytes)
            root_node = tree.root_node

            # Debug: log parse quality
            if root_node.has_error:
                error_count = sum(
                    1 for c in root_node.children if c.type == "ERROR"
                )
                logger.debug(
                    "Tree-sitter parse errors in {}: {} ERROR node(s)",
                    relative_path, error_count,
                )

            # Build module qualified name
            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )

            self.module_qn_to_file_path[module_qn] = file_path

            # Create module node and relationships
            self._create_module_node(module_qn, file_path.name, str(relative_path))
            self._create_module_relationships(
                module_qn, relative_path, structural_elements
            )

            # Parse imports
            self.import_processor.parse_imports(
                root_node, module_qn, language, queries
            )

            # Ingest functions and classes
            self._ingest_functions(root_node, module_qn, language, queries)
            self._ingest_classes(root_node, module_qn, language, queries)

            # Ingest C-specific constructs: typedefs and macros
            if language == cs.SupportedLanguage.C:
                self._ingest_c_typedefs(root_node, module_qn, queries)
                self._ingest_c_macros(root_node, module_qn, queries)

            return (root_node, language)

        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            return None

    def _create_module_node(self, module_qn: str, name: str, path: str) -> None:
        """Create a module node."""
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.MODULE,
            {
                cs.KEY_QUALIFIED_NAME: module_qn,
                cs.KEY_NAME: name,
                cs.KEY_PATH: path,
            },
        )

    def _create_module_relationships(
        self,
        module_qn: str,
        relative_path: Path,
        structural_elements: dict[Path, str | None],
    ) -> None:
        """Create relationships for the module."""
        parent_rel_path = relative_path.parent
        parent_container_qn = structural_elements.get(parent_rel_path)

        if parent_container_qn:
            parent_label, parent_key, parent_val = (
                cs.NodeLabel.PACKAGE,
                cs.KEY_QUALIFIED_NAME,
                parent_container_qn,
            )
        elif parent_rel_path != Path("."):
            parent_label, parent_key, parent_val = (
                cs.NodeLabel.FOLDER,
                cs.KEY_PATH,
                str(parent_rel_path),
            )
        else:
            parent_label, parent_key, parent_val = (
                cs.NodeLabel.PROJECT,
                cs.KEY_NAME,
                self.project_name,
            )

        self.ingestor.ensure_relationship_batch(
            (parent_label, parent_key, parent_val),
            cs.RelationshipType.CONTAINS_MODULE,
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
        )

    def _ingest_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Ingest functions from the AST."""
        lang_queries = queries.get(language)
        if not lang_queries:
            return

        func_query = lang_queries.get("functions")
        if not func_query:
            return

        # Determine the file path from module_qn for visibility analysis
        file_path = self.module_qn_to_file_path.get(module_qn)
        is_header = file_path is not None and file_path.suffix == cs.EXT_H
        is_c_lang = language == cs.SupportedLanguage.C

        try:
            cursor = QueryCursor(func_query)
            captures = cursor.captures(root_node)
            func_nodes = captures.get(cs.CAPTURE_FUNCTION, [])

            for func_node in func_nodes:
                if not isinstance(func_node, Node):
                    continue

                # Skip methods (handled by class processing)
                if self._is_method(func_node, lang_queries.get("config")):
                    continue

                func_name = self._extract_function_name(func_node)
                if not func_name:
                    continue

                func_qn = f"{module_qn}.{func_name}"

                # Derive relative file path for this function
                func_file_path = ""
                if file_path:
                    try:
                        func_file_path = str(file_path.relative_to(self.repo_path))
                    except ValueError:
                        func_file_path = str(file_path)

                func_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: func_qn,
                    cs.KEY_NAME: func_name,
                    cs.KEY_PATH: func_file_path,
                    cs.KEY_START_LINE: func_node.start_point[0] + 1,
                    cs.KEY_END_LINE: func_node.end_point[0] + 1,
                }

                # Extract C/C++ comment as docstring
                if is_c_lang:
                    c_docstring = self._extract_c_comment(func_node)
                    if c_docstring:
                        func_props[cs.KEY_DOCSTRING] = c_docstring

                # Extract API interface properties for C language
                if is_c_lang:
                    return_type = self._extract_c_return_type(func_node)
                    parameters = self._extract_c_parameters(func_node)
                    visibility = self._extract_c_visibility(func_node, is_header)
                    signature = self._build_c_signature(
                        func_name, return_type, parameters
                    )

                    func_props[cs.KEY_RETURN_TYPE] = return_type
                    func_props[cs.KEY_PARAMETERS] = parameters
                    func_props[cs.KEY_SIGNATURE] = signature
                    func_props[cs.KEY_VISIBILITY] = visibility

                    # Track header declarations for cross-file visibility
                    if is_header:
                        self._header_declarations.add(func_name)

                logger.info(f"  Found function: {func_name}")
                self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, func_props)
                self.function_registry[func_qn] = NodeType.FUNCTION
                if func_name not in self.simple_name_lookup:
                    self.simple_name_lookup[func_name] = set()
                self.simple_name_lookup[func_name].add(func_qn)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.DEFINES,
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, func_qn),
                )

            logger.debug("Module {}: {} function(s) ingested", module_qn, len(func_nodes))

        except Exception as e:
            logger.debug(f"Error ingesting functions: {e}")

    def _ingest_classes(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Ingest classes and their methods from the AST."""
        file_path = self.module_qn_to_file_path.get(module_qn)

        lang_queries = queries.get(language)
        if not lang_queries:
            return

        class_query = lang_queries.get("classes")
        if not class_query:
            return

        is_c_lang = language == cs.SupportedLanguage.C

        try:
            cursor = QueryCursor(class_query)
            captures = cursor.captures(root_node)
            class_nodes = captures.get(cs.CAPTURE_CLASS, [])

            for class_node in class_nodes:
                if not isinstance(class_node, Node):
                    continue

                class_name = self._extract_class_name(class_node)
                if not class_name:
                    continue

                class_qn = f"{module_qn}.{class_name}"

                # Derive relative file path for this class
                class_file_path = ""
                if file_path:
                    try:
                        class_file_path = str(file_path.relative_to(self.repo_path))
                    except ValueError:
                        class_file_path = str(file_path)

                class_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: class_qn,
                    cs.KEY_NAME: class_name,
                    cs.KEY_PATH: class_file_path,
                    cs.KEY_START_LINE: class_node.start_point[0] + 1,
                    cs.KEY_END_LINE: class_node.end_point[0] + 1,
                }

                # Extract C/C++ comment as docstring for struct/union/enum
                if is_c_lang:
                    c_docstring = self._extract_c_comment(class_node)
                    if c_docstring:
                        class_props[cs.KEY_DOCSTRING] = c_docstring

                # Extract C struct/union/enum members and build signature
                if is_c_lang:
                    kind = self._c_class_kind(class_node)
                    members = self._extract_c_members(class_node)
                    class_props[cs.KEY_KIND] = kind
                    class_props[cs.KEY_PARAMETERS] = members
                    class_props[cs.KEY_SIGNATURE] = self._build_c_class_signature(
                        kind, class_name, members
                    )

                logger.info(f"  Found class: {class_name}")
                self.ingestor.ensure_node_batch(cs.NodeLabel.CLASS, class_props)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.DEFINES,
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, class_qn),
                )

                # Process class methods
                self._ingest_class_methods(
                    class_node, class_qn, module_qn, language, queries
                )

            logger.debug("Module {}: {} class(es) ingested", module_qn, len(class_nodes))

        except Exception as e:
            logger.debug(f"Error ingesting classes: {e}")

    def _ingest_class_methods(
        self,
        class_node: Node,
        class_qn: str,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Ingest methods of a class."""
        lang_queries = queries.get(language)
        if not lang_queries:
            return

        func_query = lang_queries.get("functions")
        if not func_query:
            return

        try:
            body_node = class_node.child_by_field_name(cs.FIELD_BODY)
            if not body_node:
                return

            method_cursor = QueryCursor(func_query)
            captures = method_cursor.captures(body_node)

            for method_node in captures.get(cs.CAPTURE_FUNCTION, []):
                if not isinstance(method_node, Node):
                    continue

                method_name = self._extract_function_name(method_node)
                if not method_name:
                    continue

                method_qn = f"{class_qn}.{method_name}"

                method_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: method_qn,
                    cs.KEY_NAME: method_name,
                    cs.KEY_START_LINE: method_node.start_point[0] + 1,
                    cs.KEY_END_LINE: method_node.end_point[0] + 1,
                }

                logger.info(f"    Found method: {method_name}")
                self.ingestor.ensure_node_batch(cs.NodeLabel.METHOD, method_props)
                self.function_registry[method_qn] = NodeType.METHOD
                if method_name not in self.simple_name_lookup:
                    self.simple_name_lookup[method_name] = set()
                self.simple_name_lookup[method_name].add(method_qn)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, class_qn),
                    cs.RelationshipType.DEFINES_METHOD,
                    (cs.NodeLabel.METHOD, cs.KEY_QUALIFIED_NAME, method_qn),
                )

        except Exception as e:
            logger.debug(f"Error ingesting class methods: {e}")

    # -----------------------------------------------------------------
    # C/C++ comment extraction
    # -----------------------------------------------------------------

    @staticmethod
    def _extract_c_comment(func_node: Node) -> str | None:
        """Extract comment block immediately above a C/C++ function node.

        Handles:
        - Single-line: ``// comment``
        - Multi-line:  ``/* comment */``
        - Block of consecutive ``//`` lines
        - Doxygen-style: ``/** ... */`` or ``/// ...``

        Returns cleaned comment text or *None*.
        """
        comment_lines: list[str] = []

        # Walk backwards through previous siblings to collect comment nodes
        current = func_node.prev_named_sibling
        if current is None:
            current = func_node.prev_sibling

        last_end_line = func_node.start_point[0]  # 0-based line number

        while current is not None:
            if current.type != "comment":
                break

            # Check adjacency: comment must be within 1 line of the function
            # or the previous comment we already collected.
            if last_end_line - current.end_point[0] > 1:
                break

            text = safe_decode_text(current)
            if text is None:
                break

            comment_lines.insert(0, text)
            last_end_line = current.start_point[0]

            prev = current.prev_named_sibling
            if prev is None:
                prev = current.prev_sibling
            current = prev

        if not comment_lines:
            return None

        # Clean comment markers
        cleaned: list[str] = []
        for line in comment_lines:
            line = line.strip()
            # Block comment: /* ... */ or /** ... */
            if line.startswith("/*"):
                line = line[2:]
                if line.startswith("*"):  # /** doxygen */
                    line = line[1:]
            if line.endswith("*/"):
                line = line[:-2]
            # Line comment: // or ///
            if line.startswith("//"):
                line = line[2:]
                if line.startswith("/"):  # /// doxygen
                    line = line[1:]
            # Interior block comment lines: * text
            if line.startswith("*"):
                line = line[1:]

            line = line.strip()
            if line and not all(ch in "-=*#~" for ch in line):
                cleaned.append(line)

        return "\n".join(cleaned) if cleaned else None

    # -----------------------------------------------------------------
    # C language API interface extraction helpers
    # -----------------------------------------------------------------

    def _extract_c_return_type(self, func_node: Node) -> str | None:
        """Extract the return type from a C function node.

        For ``function_definition``, the return type is the ``type`` field.
        For a forward ``declaration``, the type specifiers precede the declarator.
        """
        # function_definition → type field (e.g. "int", "void", "struct foo *")
        type_node = func_node.child_by_field_name(cs.FIELD_TYPE)
        if type_node and type_node.text:
            return safe_decode_text(type_node)

        # Fallback: collect all type-specifier children that appear before the
        # declarator (covers ``static inline int func(…)`` patterns).
        parts: list[str] = []
        for child in func_node.children:
            if child.type in (
                "primitive_type",
                "sized_type_specifier",
                "type_identifier",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
            ):
                text = safe_decode_text(child)
                if text:
                    parts.append(text)
            elif child.type == cs.FIELD_DECLARATOR or child.type == "function_declarator":
                break
        return " ".join(parts) if parts else None

    def _extract_c_parameters(self, func_node: Node) -> list[str]:
        """Extract parameter list from a C function node.

        Returns a list of parameter strings like ``["int fd", "const char *buf"]``.
        """
        # Navigate to parameter_list: may be nested under declarator → function_declarator
        params_node = self._find_c_parameter_list(func_node)
        if not params_node:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "parameter_declaration":
                text = safe_decode_text(child)
                if text:
                    params.append(text)
            elif child.type == "variadic_parameter":
                params.append("...")
        return params

    def _find_c_parameter_list(self, func_node: Node) -> Node | None:
        """Locate the parameter_list node within a C function AST node."""
        # Direct: function_definition → declarator → function_declarator → parameters
        declarator = func_node.child_by_field_name(cs.FIELD_DECLARATOR)
        if declarator:
            if declarator.type == "function_declarator":
                return declarator.child_by_field_name(cs.FIELD_PARAMETERS)
            # pointer_declarator wrapping: int *func(…)
            inner = declarator.child_by_field_name(cs.FIELD_DECLARATOR)
            if inner and inner.type == "function_declarator":
                return inner.child_by_field_name(cs.FIELD_PARAMETERS)
        return None

    def _extract_c_visibility(self, func_node: Node, is_header: bool) -> str:
        """Determine C function visibility.

        Rules:
        - ``static`` keyword → "static" (file-local, private)
        - Declared in a ``.h`` header file → "public"
        - Function name found in a previously processed header → "public"
        - Otherwise → "extern" (external linkage but not declared in a header)
        """
        # Check for ``static`` storage class specifier
        for child in func_node.children:
            if child.type == self._C_STATIC_SPECIFIER:
                text = safe_decode_text(child)
                if text and "static" in text:
                    return "static"
        if is_header:
            return "public"
        # Check if this function was declared in a previously processed header
        func_name = self._extract_function_name(func_node)
        if func_name and func_name in self._header_declarations:
            return "public"
        return "extern"

    @staticmethod
    def _build_c_signature(
        name: str,
        return_type: str | None,
        parameters: list[str],
    ) -> str:
        """Build a full C function signature string."""
        ret = return_type or "void"
        params = ", ".join(parameters) if parameters else "void"
        return f"{ret} {name}({params})"

    # -----------------------------------------------------------------
    # C struct/union/enum member extraction
    # -----------------------------------------------------------------

    @staticmethod
    def _c_class_kind(class_node: Node) -> str:
        """Return the C type kind: 'struct', 'union', or 'enum'."""
        node_type = class_node.type
        if node_type == "struct_specifier":
            return "struct"
        if node_type == "union_specifier":
            return "union"
        if node_type == "enum_specifier":
            return "enum"
        return "struct"

    @staticmethod
    def _extract_c_members(class_node: Node) -> list[str]:
        """Extract member declarations from a C struct/union/enum.

        For struct/union: returns field declarations like ``["int x", "char *name"]``.
        For enum: returns enumerator names like ``["RED", "GREEN", "BLUE"]``.
        """
        members: list[str] = []
        body = class_node.child_by_field_name("body")
        if not body:
            return members

        for child in body.children:
            if child.type == "field_declaration":
                text = safe_decode_text(child)
                if text:
                    # Strip trailing semicolons
                    members.append(text.rstrip(";").strip())
            elif child.type == "enumerator":
                name_node = child.child_by_field_name("name")
                if name_node:
                    text = safe_decode_text(name_node)
                    if text:
                        members.append(text)
        return members

    @staticmethod
    def _build_c_class_signature(kind: str, name: str, members: list[str]) -> str:
        """Build a summary signature for a C struct/union/enum."""
        if not members:
            return f"{kind} {name}"
        member_str = "; ".join(members)
        return f"{kind} {name} {{ {member_str} }}"

    # -----------------------------------------------------------------
    # C typedef extraction
    # -----------------------------------------------------------------

    def _ingest_c_typedefs(
        self,
        root_node: Node,
        module_qn: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Extract typedef declarations and create Type nodes."""
        file_path = self.module_qn_to_file_path.get(module_qn)
        td_file_path = ""
        if file_path:
            try:
                td_file_path = str(file_path.relative_to(self.repo_path))
            except ValueError:
                td_file_path = str(file_path)

        lang_queries = queries.get(cs.SupportedLanguage.C)
        if not lang_queries:
            return

        typedef_query = lang_queries.get("typedefs")
        if not typedef_query:
            return

        try:
            cursor = QueryCursor(typedef_query)
            captures = cursor.captures(root_node)
            typedef_nodes = captures.get(cs.CAPTURE_TYPEDEF, [])

            for td_node in typedef_nodes:
                if not isinstance(td_node, Node):
                    continue

                td_name = self._extract_c_typedef_name(td_node)
                if not td_name:
                    continue

                td_qn = f"{module_qn}.{td_name}"
                td_text = safe_decode_text(td_node)
                signature = td_text.rstrip(";").strip() if td_text else f"typedef {td_name}"

                c_docstring = self._extract_c_comment(td_node)

                td_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: td_qn,
                    cs.KEY_NAME: td_name,
                    cs.KEY_PATH: td_file_path,
                    cs.KEY_START_LINE: td_node.start_point[0] + 1,
                    cs.KEY_END_LINE: td_node.end_point[0] + 1,
                    cs.KEY_SIGNATURE: signature,
                    cs.KEY_KIND: "typedef",
                }
                if c_docstring:
                    td_props[cs.KEY_DOCSTRING] = c_docstring

                logger.info(f"  Found typedef: {td_name}")
                self.ingestor.ensure_node_batch(cs.NodeLabel.TYPE, td_props)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.DEFINES,
                    (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, td_qn),
                )

        except Exception as e:
            logger.debug(f"Error ingesting typedefs: {e}")

    @staticmethod
    def _extract_c_typedef_name(td_node: Node) -> str | None:
        """Extract the name introduced by a typedef.

        The ``type_definition`` node has a ``declarator`` field which contains
        the new type name (a ``type_identifier``).
        """
        declarator = td_node.child_by_field_name("declarator")
        if declarator:
            if declarator.type == "type_identifier":
                return safe_decode_text(declarator)
            # Pointer typedefs: typedef int *int_ptr;
            inner = declarator.child_by_field_name("declarator")
            if inner:
                return safe_decode_text(inner)
        return None

    # -----------------------------------------------------------------
    # C macro extraction
    # -----------------------------------------------------------------

    def _ingest_c_macros(
        self,
        root_node: Node,
        module_qn: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Extract #define macro definitions and create Function nodes with kind='macro'."""
        file_path = self.module_qn_to_file_path.get(module_qn)
        macro_file_path = ""
        if file_path:
            try:
                macro_file_path = str(file_path.relative_to(self.repo_path))
            except ValueError:
                macro_file_path = str(file_path)

        lang_queries = queries.get(cs.SupportedLanguage.C)
        if not lang_queries:
            return

        macro_query = lang_queries.get("macros")
        if not macro_query:
            return

        try:
            cursor = QueryCursor(macro_query)
            captures = cursor.captures(root_node)
            macro_nodes = captures.get(cs.CAPTURE_MACRO, [])

            for macro_node in macro_nodes:
                if not isinstance(macro_node, Node):
                    continue

                macro_name = self._extract_c_macro_name(macro_node)
                if not macro_name:
                    continue

                macro_qn = f"{module_qn}.{macro_name}"
                macro_text = safe_decode_text(macro_node)
                signature = macro_text.strip() if macro_text else f"#define {macro_name}"

                c_docstring = self._extract_c_comment(macro_node)

                macro_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: macro_qn,
                    cs.KEY_NAME: macro_name,
                    cs.KEY_PATH: macro_file_path,
                    cs.KEY_START_LINE: macro_node.start_point[0] + 1,
                    cs.KEY_END_LINE: macro_node.end_point[0] + 1,
                    cs.KEY_SIGNATURE: signature,
                    cs.KEY_KIND: "macro",
                    cs.KEY_VISIBILITY: "public",
                }
                if c_docstring:
                    macro_props[cs.KEY_DOCSTRING] = c_docstring

                logger.info(f"  Found macro: {macro_name}")
                self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, macro_props)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.DEFINES,
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, macro_qn),
                )

            logger.debug("Module {}: {} macro(s) ingested", module_qn, len(macro_nodes))

        except Exception as e:
            logger.debug(f"Error ingesting macros: {e}")

    @staticmethod
    def _extract_c_macro_name(macro_node: Node) -> str | None:
        """Extract the macro name from a preproc_def node."""
        name_node = macro_node.child_by_field_name("name")
        if name_node:
            return safe_decode_text(name_node)
        return None

    def _extract_function_name(self, func_node: Node) -> str | None:
        """Extract function name from a function node."""
        # Try standard name field first
        name_node = func_node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return safe_decode_text(name_node)

        # For C language: function_definition -> declarator -> function_declarator -> declarator (name)
        declarator = func_node.child_by_field_name(cs.FIELD_DECLARATOR)
        if declarator:
            if declarator.type == "function_declarator":
                name_node = declarator.child_by_field_name(cs.FIELD_DECLARATOR)
            else:
                name_node = declarator
            if name_node and name_node.text:
                return safe_decode_text(name_node)

        return None

    def _extract_class_name(self, class_node: Node) -> str | None:
        """Extract class name from a class node."""
        name_node = class_node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return safe_decode_text(name_node)
        return None

    def _is_method(self, func_node: Node, lang_config) -> bool:
        """Check if a function node is a method."""
        if not lang_config:
            return False

        current = func_node.parent
        if not isinstance(current, Node):
            return False

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.class_node_types:
                return True
            current = current.parent
        return False

    def process_dependencies(self, filepath: Path) -> None:
        """Process dependency files."""
        logger.info(f"Processing dependencies: {filepath}")

    def process_all_method_overrides(self) -> None:
        """Process all method overrides."""
        pass
