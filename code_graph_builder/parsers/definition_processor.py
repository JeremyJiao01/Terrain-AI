"""Definition processor for ingesting code definitions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, QueryCursor

from .. import constants as cs
from ..services import IngestorProtocol
from ..types import LanguageQueries, NodeType, PropertyDict, SimpleNameLookup
from .utils import safe_decode_text

if TYPE_CHECKING:
    from ..types import FunctionRegistryTrieProtocol
    from .import_processor import ImportProcessor


class DefinitionProcessor:
    """Process file definitions (functions, classes, methods)."""

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

            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            root_node = tree.root_node

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

                func_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: func_qn,
                    cs.KEY_NAME: func_name,
                    cs.KEY_START_LINE: func_node.start_point[0] + 1,
                    cs.KEY_END_LINE: func_node.end_point[0] + 1,
                }

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
        lang_queries = queries.get(language)
        if not lang_queries:
            return

        class_query = lang_queries.get("classes")
        if not class_query:
            return

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

                class_props: PropertyDict = {
                    cs.KEY_QUALIFIED_NAME: class_qn,
                    cs.KEY_NAME: class_name,
                    cs.KEY_START_LINE: class_node.start_point[0] + 1,
                    cs.KEY_END_LINE: class_node.end_point[0] + 1,
                }

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
