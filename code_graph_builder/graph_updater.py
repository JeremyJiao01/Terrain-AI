"""Graph updater for building code knowledge graphs."""

from __future__ import annotations

import sys
from collections import OrderedDict, defaultdict
from collections.abc import Callable, ItemsView, KeysView
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, Parser

from . import constants as cs
from .language_spec import get_language_spec
from .parsers.factory import ProcessorFactory
from .services import IngestorProtocol
from .types import (
    FunctionRegistry,
    LanguageQueries,
    NodeType,
    PropertyDict,
    QualifiedName,
    SimpleNameLookup,
    TrieNode,
)

if TYPE_CHECKING:
    from .embeddings.qwen3_embedder import BaseEmbedder
    from .embeddings.vector_store import VectorStore
    from .utils.path_utils import should_skip_path


class FunctionRegistryTrie:
    """Trie-based registry for efficient function lookup."""

    def __init__(self, simple_name_lookup: SimpleNameLookup | None = None) -> None:
        self.root: TrieNode = {}
        self._entries: FunctionRegistry = {}
        self._simple_name_lookup = simple_name_lookup

    def insert(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        self._entries[qualified_name] = func_type

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        current: TrieNode = self.root

        for part in parts:
            if part not in current:
                current[part] = {}
            child = current[part]
            assert isinstance(child, dict)
            current = child

        current[cs.TRIE_TYPE_KEY] = func_type
        current[cs.TRIE_QN_KEY] = qualified_name

    def get(
        self, qualified_name: QualifiedName, default: NodeType | None = None
    ) -> NodeType | None:
        return self._entries.get(qualified_name, default)

    def __contains__(self, qualified_name: QualifiedName) -> bool:
        return qualified_name in self._entries

    def __getitem__(self, qualified_name: QualifiedName) -> NodeType:
        return self._entries[qualified_name]

    def __setitem__(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        self.insert(qualified_name, func_type)

    def __delitem__(self, qualified_name: QualifiedName) -> None:
        if qualified_name not in self._entries:
            return

        del self._entries[qualified_name]

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        self._cleanup_trie_path(parts, self.root)

    def _cleanup_trie_path(self, parts: list[str], node: TrieNode) -> bool:
        if not parts:
            node.pop(cs.TRIE_QN_KEY, None)
            node.pop(cs.TRIE_TYPE_KEY, None)
            return not node

        part = parts[0]
        if part not in node:
            return False

        child = node[part]
        assert isinstance(child, dict)
        if self._cleanup_trie_path(parts[1:], child):
            del node[part]

        is_endpoint = cs.TRIE_QN_KEY in node
        has_children = any(not key.startswith(cs.TRIE_INTERNAL_PREFIX) for key in node)
        return not has_children and not is_endpoint

    def _navigate_to_prefix(self, prefix: str) -> TrieNode | None:
        parts = prefix.split(cs.SEPARATOR_DOT) if prefix else []
        current: TrieNode = self.root
        for part in parts:
            if part not in current:
                return None
            child = current[part]
            assert isinstance(child, dict)
            current = child
        return current

    def _collect_from_subtree(
        self,
        node: TrieNode,
        filter_fn: Callable[[QualifiedName], bool] | None = None,
    ) -> list[tuple[QualifiedName, NodeType]]:
        results: list[tuple[QualifiedName, NodeType]] = []

        def dfs(n: TrieNode) -> None:
            if cs.TRIE_QN_KEY in n:
                qn = n[cs.TRIE_QN_KEY]
                func_type = n[cs.TRIE_TYPE_KEY]
                assert isinstance(qn, str) and isinstance(func_type, NodeType)
                if filter_fn is None or filter_fn(qn):
                    results.append((qn, func_type))

            for key, child in n.items():
                if not key.startswith(cs.TRIE_INTERNAL_PREFIX):
                    assert isinstance(child, dict)
                    dfs(child)

        dfs(node)
        return results

    def keys(self) -> KeysView[QualifiedName]:
        return self._entries.keys()

    def items(self) -> ItemsView[QualifiedName, NodeType]:
        return self._entries.items()

    def __len__(self) -> int:
        return len(self._entries)

    def find_with_prefix_and_suffix(
        self, prefix: str, suffix: str
    ) -> list[QualifiedName]:
        node = self._navigate_to_prefix(prefix)
        if node is None:
            return []
        suffix_pattern = f".{suffix}"
        matches = self._collect_from_subtree(
            node, lambda qn: qn.endswith(suffix_pattern)
        )
        return [qn for qn, _ in matches]

    def find_ending_with(self, suffix: str) -> list[QualifiedName]:
        if self._simple_name_lookup is not None and suffix in self._simple_name_lookup:
            return list(self._simple_name_lookup[suffix])
        return [qn for qn in self._entries.keys() if qn.endswith(f".{suffix}")]

    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]:
        node = self._navigate_to_prefix(prefix)
        return [] if node is None else self._collect_from_subtree(node)


class BoundedASTCache:
    """LRU cache for AST nodes with memory limits."""

    def __init__(
        self,
        max_entries: int = 1000,
        max_memory_mb: int = 500,
    ):
        self.cache: OrderedDict[Path, tuple[Node, cs.SupportedLanguage]] = OrderedDict()
        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_mb * cs.BYTES_PER_MB

    def __setitem__(self, key: Path, value: tuple[Node, cs.SupportedLanguage]) -> None:
        if key in self.cache:
            del self.cache[key]

        self.cache[key] = value
        self._enforce_limits()

    def __getitem__(self, key: Path) -> tuple[Node, cs.SupportedLanguage]:
        value = self.cache[key]
        self.cache.move_to_end(key)
        return value

    def __delitem__(self, key: Path) -> None:
        if key in self.cache:
            del self.cache[key]

    def __contains__(self, key: Path) -> bool:
        return key in self.cache

    def items(self) -> ItemsView[Path, tuple[Node, cs.SupportedLanguage]]:
        return self.cache.items()

    def _enforce_limits(self) -> None:
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)

        if self._should_evict_for_memory():
            entries_to_remove = max(1, len(self.cache) // 10)
            for _ in range(entries_to_remove):
                if self.cache:
                    self.cache.popitem(last=False)

    def _should_evict_for_memory(self) -> bool:
        try:
            cache_size = sum(sys.getsizeof(v) for v in self.cache.values())
            return cache_size > self.max_memory_bytes
        except Exception:
            return len(self.cache) > int(self.max_entries * 0.8)


class GraphUpdater:
    """Main coordinator for building code knowledge graphs."""

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        parsers: dict[cs.SupportedLanguage, Parser],
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        unignore_paths: frozenset[str] | None = None,
        exclude_paths: frozenset[str] | None = None,
        embedder: BaseEmbedder | None = None,
        vector_store: VectorStore | None = None,
        embedding_config: dict[str, bool | int | str] | None = None,
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.parsers = parsers
        self.queries = queries
        self.project_name = repo_path.resolve().name
        self.simple_name_lookup: SimpleNameLookup = defaultdict(set)
        self.function_registry = FunctionRegistryTrie(
            simple_name_lookup=self.simple_name_lookup
        )
        self.ast_cache = BoundedASTCache()
        self.unignore_paths = unignore_paths
        self.exclude_paths = exclude_paths

        self.embedder = embedder
        self.vector_store = vector_store
        self.embedding_config = embedding_config or {}
        self._embedding_enabled = self.embedding_config.get("enabled", False)

        self.factory = ProcessorFactory(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            project_name=self.project_name,
            queries=self.queries,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
            ast_cache=self.ast_cache,
            unignore_paths=self.unignore_paths,
            exclude_paths=self.exclude_paths,
        )

    def _is_dependency_file(self, file_name: str, filepath: Path) -> bool:
        return (
            file_name.lower() in cs.DEPENDENCY_FILES
            or filepath.suffix.lower() == ".csproj"
        )

    def run(self) -> None:
        """Run the graph building process."""
        logger.info(f"Building graph for project: {self.project_name}")

        # Pass 1: Structure
        logger.info("Pass 1: Identifying project structure")
        self.factory.structure_processor.identify_structure()

        # Pass 2: Files
        logger.info("Pass 2: Processing files")
        self._process_files()

        logger.info(f"Found {len(self.function_registry)} functions")

        # Pass 3: Calls
        logger.info("Pass 3: Processing function calls")
        self._process_function_calls()

        # Process method overrides
        self.factory.definition_processor.process_all_method_overrides()

        # Pass 4: Semantic Embeddings (optional)
        if self._embedding_enabled and self.embedder and self.vector_store:
            logger.info("Pass 4: Generating semantic embeddings")
            self._generate_semantic_embeddings()

        logger.info("Analysis complete")
        self.ingestor.flush_all()

    def _process_files(self) -> None:
        """Process all files in the repository."""
        try:
            from .utils.path_utils import should_skip_path
        except ImportError:
            # Fallback if utils not available
            def should_skip_path(
                filepath: Path,
                repo_path: Path,
                exclude_paths: frozenset[str] | None = None,
                unignore_paths: frozenset[str] | None = None,
            ) -> bool:
                rel_path = filepath.relative_to(repo_path)
                path_str = str(rel_path)

                # Skip common directories
                skip_dirs = {".git", "__pycache__", "node_modules", "venv", ".venv", ".pytest_cache"}
                if any(part in skip_dirs for part in rel_path.parts):
                    return True

                # Skip excluded paths
                if exclude_paths:
                    for pattern in exclude_paths:
                        if pattern in path_str:
                            return True

                return False

        for filepath in self.repo_path.rglob("*"):
            if filepath.is_file() and not should_skip_path(
                filepath,
                self.repo_path,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ):
                lang_config = get_language_spec(filepath.suffix)
                if (
                    lang_config
                    and isinstance(lang_config.language, cs.SupportedLanguage)
                    and lang_config.language in self.parsers
                ):
                    result = self.factory.definition_processor.process_file(
                        filepath,
                        lang_config.language,
                        self.queries,
                        self.factory.structure_processor.structural_elements,
                    )
                    if result:
                        root_node, language = result
                        self.ast_cache[filepath] = (root_node, language)
                elif self._is_dependency_file(filepath.name, filepath):
                    self.factory.definition_processor.process_dependencies(filepath)

                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )

    def _process_function_calls(self) -> None:
        """Process function calls in all cached ASTs."""
        ast_cache_items = list(self.ast_cache.items())
        for file_path, (root_node, language) in ast_cache_items:
            self.factory.call_processor.process_calls_in_file(
                file_path, root_node, language, self.queries
            )

    def _generate_semantic_embeddings(self) -> None:
        """Generate semantic embeddings for functions and classes.

        This is Pass 4 of the graph building process.
        Extracts source code for each function/method and generates
        embeddings using the configured embedder.
        """
        if not self.embedder or not self.vector_store:
            logger.warning("Embedder or vector store not configured, skipping embeddings")
            return

        try:
            from .embeddings.vector_store import VectorRecord

            records_to_store: list[VectorRecord] = []
            texts_to_embed: list[str] = []
            node_info: list[tuple[int, str, PropertyDict]] = []

            batch_size = self.embedding_config.get("batch_size", 32)

            for qn, node_type in self.function_registry.items():
                if node_type not in (NodeType.FUNCTION, NodeType.METHOD, NodeType.CLASS):
                    continue

                try:
                    source_code = self._extract_source_for_qualified_name(qn)
                    if not source_code:
                        continue

                    node_id = self._get_node_id_for_qualified_name(qn)
                    if node_id is None:
                        continue

                    texts_to_embed.append(source_code)
                    node_info.append((node_id, qn, {"type": str(node_type)}))

                    if len(texts_to_embed) >= batch_size:
                        self._embed_and_store_batch(
                            texts_to_embed, node_info, records_to_store
                        )
                        texts_to_embed = []
                        node_info = []

                except Exception as e:
                    logger.warning(f"Failed to prepare embedding for {qn}: {e}")
                    continue

            if texts_to_embed:
                self._embed_and_store_batch(texts_to_embed, node_info, records_to_store)

            stats = self.vector_store.get_stats()
            logger.info(f"Generated embeddings for {stats['count']} code entities")

        except Exception as e:
            logger.error(f"Failed to generate semantic embeddings: {e}")

    def _embed_and_store_batch(
        self,
        texts: list[str],
        node_info: list[tuple[int, str, PropertyDict]],
        records: list,
    ) -> None:
        """Embed a batch of texts and store in vector store.

        Args:
            texts: Source code texts to embed
            node_info: Tuple of (node_id, qualified_name, metadata)
            records: Accumulated records list
        """
        from .embeddings.vector_store import VectorRecord

        if not self.embedder or not self.vector_store:
            return

        try:
            embeddings = self.embedder.embed_documents(texts, show_progress=False)

            for (node_id, qn, metadata), embedding in zip(node_info, embeddings):
                record = VectorRecord(
                    node_id=node_id,
                    qualified_name=qn,
                    embedding=embedding,
                    metadata=metadata,
                )
                records.append(record)

            self.vector_store.store_embeddings_batch(records)
            records.clear()

        except Exception as e:
            logger.warning(f"Failed to embed batch: {e}")

    def _extract_source_for_qualified_name(self, qualified_name: str) -> str | None:
        """Extract source code for a qualified name.

        Args:
            qualified_name: Fully qualified name of the entity

        Returns:
            Source code string or None if not found
        """
        try:
            parts = qualified_name.split(cs.SEPARATOR_DOT)
            if len(parts) < 2:
                return None

            file_path = self._resolve_file_from_qn(parts)
            if not file_path or not file_path.exists():
                return None

            if file_path not in self.ast_cache:
                return None

            root_node, language = self.ast_cache[file_path]

            source_code = file_path.read_text(encoding="utf-8", errors="ignore")

            entity_name = parts[-1]
            lines = source_code.split("\n")

            for i, line in enumerate(lines):
                if entity_name in line and self._is_definition_line(line, entity_name):
                    start_line = max(0, i - 2)
                    end_line = min(len(lines), i + 50)
                    return "\n".join(lines[start_line:end_line])

            return source_code[:2000]

        except Exception as e:
            logger.debug(f"Failed to extract source for {qualified_name}: {e}")
            return None

    def _is_definition_line(self, line: str, name: str) -> bool:
        """Check if a line contains a definition for the given name.

        Args:
            line: Source code line
            name: Entity name to check

        Returns:
            True if this looks like a definition line
        """
        stripped = line.strip()
        keywords = ["def ", "class ", "function ", "fn ", "func "]
        return any(kw in stripped for kw in keywords) and name in stripped

    def _resolve_file_from_qn(self, parts: list[str]) -> Path | None:
        """Resolve file path from qualified name parts.

        Args:
            parts: Parts of the qualified name

        Returns:
            Path object or None if not resolved
        """
        try:
            if parts[0] != self.project_name:
                return None

            relative_parts = parts[1:]

            for i in range(len(relative_parts), 0, -1):
                candidate = self.repo_path.joinpath(*relative_parts[:i])
                if candidate.exists() and candidate.is_file():
                    return candidate

                for ext in [".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp", ".c"]:
                    candidate_with_ext = self.repo_path.joinpath(
                        *relative_parts[:i]
                    ).with_suffix(ext)
                    if candidate_with_ext.exists():
                        return candidate_with_ext

            return None

        except Exception:
            return None

    def _get_node_id_for_qualified_name(self, qualified_name: str) -> int | None:
        """Get node ID for a qualified name from the ingestor.

        Args:
            qualified_name: Fully qualified name

        Returns:
            Node ID or None if not found
        """
        try:
            if hasattr(self.ingestor, "_node_id_cache"):
                cache = self.ingestor._node_id_cache
                for key, node_id in cache.items():
                    if isinstance(key, tuple) and len(key) >= 3:
                        if key[2] == qualified_name:
                            return node_id

            return hash(qualified_name) % (2**31)

        except Exception:
            return None

    def remove_file_from_state(self, file_path: Path) -> None:
        """Remove a file from the internal state."""
        logger.debug(f"Removing state for: {file_path}")

        if file_path in self.ast_cache:
            del self.ast_cache[file_path]

        relative_path = file_path.relative_to(self.repo_path)
        path_parts = (
            relative_path.parent.parts
            if file_path.name == cs.INIT_PY
            else relative_path.with_suffix("").parts
        )
        module_qn_prefix = cs.SEPARATOR_DOT.join([self.project_name, *path_parts])

        qns_to_remove = set()

        for qn in list(self.function_registry.keys()):
            if qn.startswith(f"{module_qn_prefix}.") or qn == module_qn_prefix:
                qns_to_remove.add(qn)
                del self.function_registry[qn]

        for simple_name, qn_set in self.simple_name_lookup.items():
            original_count = len(qn_set)
            new_qn_set = qn_set - qns_to_remove
            if len(new_qn_set) < original_count:
                self.simple_name_lookup[simple_name] = new_qn_set
