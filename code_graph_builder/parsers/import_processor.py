"""Code Graph Builder - Import Processor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, QueryCursor

from .. import constants as cs
from ..parsers.utils import safe_decode_text
from ..services import IngestorProtocol
from ..types import FunctionRegistryTrieProtocol

if TYPE_CHECKING:
    from ..types import LanguageQueries


class ImportProcessor:
    """Process import statements in source code."""

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: IngestorProtocol | None = None,
        function_registry: FunctionRegistryTrieProtocol | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.project_name = project_name
        self.ingestor = ingestor
        self.function_registry = function_registry
        self.import_mapping: dict[str, dict[str, str]] = {}

    def parse_imports(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """Parse imports from a file."""
        if language not in queries:
            return

        lang_queries = queries[language]
        imports_query = lang_queries.get(cs.QUERY_IMPORTS)
        if not imports_query:
            return

        self.import_mapping[module_qn] = {}

        try:
            cursor = QueryCursor(imports_query)
            captures = cursor.captures(root_node)

            match language:
                case cs.SupportedLanguage.PYTHON:
                    self._parse_python_imports(captures, module_qn)
                case cs.SupportedLanguage.JS | cs.SupportedLanguage.TS:
                    self._parse_js_ts_imports(captures, module_qn)
                case cs.SupportedLanguage.JAVA:
                    self._parse_java_imports(captures, module_qn)
                case cs.SupportedLanguage.RUST:
                    self._parse_rust_imports(captures, module_qn)
                case cs.SupportedLanguage.GO:
                    self._parse_go_imports(captures, module_qn)
                case cs.SupportedLanguage.C | cs.SupportedLanguage.CPP:
                    self._parse_c_cpp_imports(captures, module_qn)
                case _:
                    pass

            logger.debug(f"Parsed {len(self.import_mapping[module_qn])} imports for {module_qn}")

            if self.ingestor:
                for alias, full_name in self.import_mapping[module_qn].items():
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                        cs.RelationshipType.IMPORTS,
                        (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, full_name),
                    )

        except Exception as e:
            logger.warning(f"Failed to parse imports for {module_qn}: {e}")

    def _parse_python_imports(self, captures: dict, module_qn: str) -> None:
        """Parse Python import statements."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])
        import_from_nodes = captures.get(cs.CAPTURE_IMPORT_FROM, [])

        for node in import_nodes + import_from_nodes:
            if not isinstance(node, Node):
                continue

            if node.type == "import_statement":
                self._handle_python_import(node, module_qn)
            elif node.type == "import_from_statement":
                self._handle_python_from_import(node, module_qn)

    def _handle_python_import(self, node: Node, module_qn: str) -> None:
        """Handle 'import xxx' or 'import xxx as yyy'."""
        for child in node.named_children:
            if child.type == "dotted_name":
                name = self._get_dotted_name(child)
                if name:
                    full_qn = f"{self.project_name}.{name.replace('.', cs.SEPARATOR_DOT)}"
                    self.import_mapping[module_qn][name.split(cs.SEPARATOR_DOT)[0]] = full_qn

    def _handle_python_from_import(self, node: Node, module_qn: str) -> None:
        """Handle 'from xxx import yyy'."""
        module_node = None
        for child in node.children:
            if child.type == "dotted_name":
                module_node = child
                break

        if not module_node:
            return

        module_name = self._get_dotted_name(module_node)
        if not module_name:
            return

        module_prefix = f"{self.project_name}.{module_name.replace('.', cs.SEPARATOR_DOT)}"

        for child in node.named_children:
            if child.type == "imported_name" or child.type == "identifier":
                name = safe_decode_text(child)
                if name:
                    full_qn = f"{module_prefix}.{name}"
                    self.import_mapping[module_qn][name] = full_qn

    def _parse_js_ts_imports(self, captures: dict, module_qn: str) -> None:
        """Parse JavaScript/TypeScript imports."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])

        for node in import_nodes:
            if not isinstance(node, Node):
                continue

            if node.type == "import_statement":
                self._handle_js_ts_import(node, module_qn)

    def _handle_js_ts_import(self, node: Node, module_qn: str) -> None:
        """Handle ES6 import statements."""
        source_node = None
        for child in node.children:
            if child.type == "string":
                source_node = child
                break

        if not source_node:
            return

        source = safe_decode_text(source_node)
        if not source:
            return

        source = source.strip("'\"")

        for child in node.named_children:
            if child.type == "import_clause":
                self._process_js_import_clause(child, source, module_qn)

    def _process_js_import_clause(self, node: Node, source: str, module_qn: str) -> None:
        """Process import clause (default, named, or namespace imports)."""
        name = safe_decode_text(node)
        if name:
            self.import_mapping[module_qn][name] = source

    def _parse_java_imports(self, captures: dict, module_qn: str) -> None:
        """Parse Java imports."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])

        for node in import_nodes:
            if not isinstance(node, Node):
                continue

            scoped_name = None
            for child in node.named_children:
                if child.type == "scoped_identifier":
                    scoped_name = safe_decode_text(child)
                    break
                elif child.type == "identifier":
                    scoped_name = safe_decode_text(child)

            if scoped_name:
                parts = scoped_name.split(cs.SEPARATOR_DOT)
                if parts:
                    self.import_mapping[module_qn][parts[-1]] = scoped_name.replace(
                        cs.SEPARATOR_DOT, "."
                    )

    def _parse_rust_imports(self, captures: dict, module_qn: str) -> None:
        """Parse Rust use statements."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])

        for node in import_nodes:
            if not isinstance(node, Node):
                continue

            if node.type == "use_declaration":
                self._handle_rust_use(node, module_qn)

    def _handle_rust_use(self, node: Node, module_qn: str) -> None:
        """Handle Rust use statements."""
        for child in node.named_children:
            if child.type == "scoped_use_list":
                prefix = None
                use_list = None
                for c in child.children:
                    if c.type == "identifier" or c.type == "scoped_identifier":
                        prefix = safe_decode_text(c)
                    elif c.type == "use_list":
                        use_list = c

                if prefix and use_list:
                    for item in use_list.named_children:
                        name = safe_decode_text(item)
                        if name:
                            full_qn = f"{prefix}::{name}"
                            self.import_mapping[module_qn][name] = full_qn
            elif child.type in ("scoped_identifier", "identifier"):
                name = safe_decode_text(child)
                if name:
                    parts = name.split("::")
                    if parts:
                        self.import_mapping[module_qn][parts[-1]] = name

    def _parse_go_imports(self, captures: dict, module_qn: str) -> None:
        """Parse Go imports."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])

        for node in import_nodes:
            if not isinstance(node, Node):
                continue

            if node.type == "import_declaration":
                for child in node.named_children:
                    if child.type == "import_spec":
                        self._handle_go_import_spec(child, module_qn)
                    elif child.type == "import_spec_list":
                        for spec in child.named_children:
                            if spec.type == "import_spec":
                                self._handle_go_import_spec(spec, module_qn)

    def _handle_go_import_spec(self, node: Node, module_qn: str) -> None:
        """Handle Go import specification."""
        alias = None
        path = None

        for child in node.named_children:
            if child.type == "package_identifier":
                alias = safe_decode_text(child)
            elif child.type == "interpreted_string_literal":
                path = safe_decode_text(child)

        if path:
            path = path.strip('"')
            key = alias if alias else path.split("/")[-1]
            self.import_mapping[module_qn][key] = path

    def _parse_c_cpp_imports(self, captures: dict, module_qn: str) -> None:
        """Parse C/C++ #include directives."""
        import_nodes = captures.get(cs.CAPTURE_IMPORT, [])

        for node in import_nodes:
            if not isinstance(node, Node):
                continue

            if node.type == "preproc_include":
                for child in node.named_children:
                    if child.type in ("string_literal", "system_lib_string"):
                        header = safe_decode_text(child)
                        if header:
                            header = header.strip('"<>')
                            key = header.replace(".", "_")
                            self.import_mapping[module_qn][key] = header

    def _get_dotted_name(self, node: Node) -> str | None:
        """Get dotted name from a node."""
        parts = []
        for child in node.children:
            if child.type == "identifier":
                name = safe_decode_text(child)
                if name:
                    parts.append(name)
        return cs.SEPARATOR_DOT.join(parts) if parts else None

    def get_import_mapping(self, module_qn: str) -> dict[str, str]:
        """Get import mapping for a module."""
        return self.import_mapping.get(module_qn, {})
