"""RAG Engine for code graph-based retrieval and generation.

This module provides the main RAG engine that combines semantic search,
graph queries, and LLM generation for code analysis.

The RAG flow:
1. Semantic search to find relevant code entities
2. Graph traversal to gather context (callers, callees, related)
3. Prompt assembly with retrieved context
4. LLM generation (Kimi k2.5)
5. Markdown output generation

Examples:
    >>> from code_graph_builder.rag import RAGConfig, create_rag_engine
    >>> from code_graph_builder.embeddings import create_embedder, create_vector_store
    >>> from code_graph_builder.services import MemgraphIngestor
    >>>
    >>> config = RAGConfig.from_env()
    >>> embedder = create_embedder()
    >>> vector_store = create_vector_store(backend="memory", dimension=1536)
    >>>
    >>> with MemgraphIngestor("localhost", 7687) as graph_service:
    ...     engine = create_rag_engine(
    ...         config=config,
    ...         embedder=embedder,
    ...         vector_store=vector_store,
    ...         graph_service=graph_service,
    ...     )
    ...     result = engine.query("Explain the authentication flow")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from ..embeddings.qwen3_embedder import BaseEmbedder
from ..embeddings.vector_store import VectorStore
from ..tools.graph_query import GraphQueryService, create_graph_query_service
from ..tools.semantic_search import (
    SemanticSearchService,
    create_semantic_search_service,
)
from .config import RAGConfig
from .kimi_client import KimiClient, create_kimi_client
from .markdown_generator import (
    AnalysisResult,
    MarkdownGenerator,
    SourceReference,
)
from .prompt_templates import (
    CodeContext,
    RAGPrompts,
    create_code_context,
)

if TYPE_CHECKING:
    from ..types import ResultRow


@dataclass
class RAGResult:
    """Result from RAG query.

    Attributes:
        query: Original user query
        response: Generated response text
        sources: List of source references used
        contexts: List of code contexts retrieved
        metadata: Additional metadata about the query
    """

    query: str
    response: str
    sources: list[SourceReference] = field(default_factory=list)
    contexts: list[CodeContext] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self, generator: MarkdownGenerator | None = None) -> str:
        """Convert result to markdown document.

        Args:
            generator: Optional markdown generator

        Returns:
            Markdown document as string
        """
        if generator is None:
            generator = MarkdownGenerator()

        analysis_result = AnalysisResult(
            query=self.query,
            response=self.response,
            sources=self.sources,
            metadata=self.metadata,
        )

        return generator.generate_analysis_doc(
            title="Code Analysis",
            result=analysis_result,
        )


class GraphServiceProtocol(Protocol):
    """Protocol for graph service operations."""

    def fetch_all(self, query: str, params: dict | None = None) -> list[ResultRow]: ...


class RAGEngine:
    """RAG Engine for code analysis.

    Combines semantic search, graph queries, and LLM generation
to provide intelligent code analysis capabilities.

    Args:
        config: RAG configuration
        kimi_client: Kimi API client
        semantic_service: Semantic search service
        graph_service: Graph query service
        prompts: RAG prompts

    Example:
        >>> engine = RAGEngine(
        ...     config=config,
        ...     kimi_client=kimi_client,
        ...     semantic_service=semantic_service,
        ...     graph_service=graph_service,
        ... )
        >>> result = engine.query("How does authentication work?")
    """

    def __init__(
        self,
        config: RAGConfig,
        kimi_client: KimiClient,
        semantic_service: SemanticSearchService,
        graph_service: GraphQueryService,
    ):
        self.config = config
        self.kimi_client = kimi_client
        self.semantic_service = semantic_service
        self.graph_service = graph_service
        self.prompts = RAGPrompts()
        self.markdown_generator = MarkdownGenerator()

        logger.info("Initialized RAGEngine")

    def query(
        self,
        query: str,
        top_k: int | None = None,
        include_graph_context: bool = True,
    ) -> RAGResult:
        """Execute a RAG query.

        Args:
            query: User query string
            top_k: Number of results to retrieve (overrides config)
            include_graph_context: Whether to include graph relationships

        Returns:
            RAGResult with response and metadata
        """
        logger.info(f"RAG query: {query}")

        # Step 1: Semantic search
        semantic_results = self._semantic_search(query, top_k)
        if not semantic_results:
            return RAGResult(
                query=query,
                response="No relevant code found for your query.",
                metadata={"semantic_results": 0},
            )

        # Step 2: Build code contexts
        contexts = self._build_contexts(semantic_results, include_graph_context)

        # Step 3: Generate response
        response = self._generate_response(query, contexts)

        # Step 4: Build source references
        sources = self._build_sources(contexts)

        return RAGResult(
            query=query,
            response=response,
            sources=sources,
            contexts=contexts,
            metadata={
                "semantic_results": len(semantic_results),
                "contexts": len(contexts),
                "model": self.config.moonshot.model,
            },
        )

    def explain_code(
        self,
        qualified_name: str,
        include_related: bool = True,
    ) -> RAGResult:
        """Explain a specific code entity.

        Args:
            qualified_name: Fully qualified name of the entity
            include_related: Whether to include related entities

        Returns:
            RAGResult with explanation
        """
        logger.info(f"Explaining code: {qualified_name}")

        # Fetch entity from graph
        node = self.graph_service.fetch_node_by_qualified_name(qualified_name)
        if not node:
            return RAGResult(
                query=f"Explain {qualified_name}",
                response=f"Entity '{qualified_name}' not found in the code graph.",
            )

        # Build context
        context = self._node_to_context(node)

        # Get related entities if requested
        contexts = [context]
        if include_related:
            related = self._get_related_contexts(node.node_id)
            contexts.extend(related)

        # Generate explanation
        system_prompt = self.prompts.analysis.get_system_prompt()
        user_prompt = self.prompts.analysis.format_explain_prompt(context)

        chat_response = self.kimi_client.chat_with_messages([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        sources = [SourceReference(
            name=node.name,
            qualified_name=node.qualified_name,
            file_path=node.path or "",
            line_start=node.start_line,
            line_end=node.end_line,
            entity_type=node.type,
        )]

        return RAGResult(
            query=f"Explain {qualified_name}",
            response=chat_response.content,
            sources=sources,
            contexts=contexts,
            metadata={
                "entity": qualified_name,
                "type": node.type,
            },
        )

    def analyze_architecture(
        self,
        module_name: str,
    ) -> RAGResult:
        """Analyze architecture of a module.

        Args:
            module_name: Module or package name

        Returns:
            RAGResult with architecture analysis
        """
        logger.info(f"Analyzing architecture: {module_name}")

        # Query for module entities
        query = """
            MATCH (n)
            WHERE n.qualified_name STARTS WITH $module_name
            RETURN n.node_id AS node_id,
                   n.qualified_name AS qualified_name,
                   n.name AS name,
                   labels(n) AS labels,
                   n.path AS path,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.source_code AS source_code
            LIMIT 20
        """

        results = self.graph_service.execute_cypher(query, {"module_name": module_name})

        if not results:
            return RAGResult(
                query=f"Analyze architecture of {module_name}",
                response=f"No entities found for module '{module_name}'.",
            )

        # Build contexts
        contexts = []
        for row in results:
            source_code = row.get("source_code", "")
            if source_code:
                contexts.append(create_code_context(
                    source_code=source_code,
                    file_path=row.get("path"),
                    qualified_name=row.get("qualified_name"),
                    entity_type=row.get("labels", ["Unknown"])[0] if row.get("labels") else "Unknown",
                ))

        # Generate analysis
        system_prompt = self.prompts.analysis.get_system_prompt()
        user_prompt = self.prompts.analysis.format_architecture_prompt(
            contexts[0] if len(contexts) == 1 else
            "\n\n".join(f"### Entity {i+1}\n{ctx.format_context()}"
                       for i, ctx in enumerate(contexts[:5]))
        )

        chat_response = self.kimi_client.chat_with_messages([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        sources = [
            SourceReference(
                name=row.get("name", ""),
                qualified_name=row.get("qualified_name", ""),
                file_path=row.get("path", ""),
                entity_type=row.get("labels", ["Unknown"])[0] if row.get("labels") else "Unknown",
            )
            for row in results[:10]
        ]

        return RAGResult(
            query=f"Analyze architecture of {module_name}",
            response=chat_response.content,
            sources=sources,
            contexts=contexts,
            metadata={
                "module": module_name,
                "entities_analyzed": len(results),
            },
        )

    def _semantic_search(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[Any]:
        """Execute semantic search.

        Args:
            query: Search query
            top_k: Number of results

        Returns:
            List of semantic search results
        """
        k = top_k or self.config.retrieval.semantic_top_k
        results = self.semantic_service.search(query, top_k=k)
        logger.debug(f"Semantic search returned {len(results)} results")
        return results

    def _build_contexts(
        self,
        semantic_results: list[Any],
        include_graph_context: bool,
    ) -> list[CodeContext]:
        """Build code contexts from semantic results.

        Args:
            semantic_results: Results from semantic search
            include_graph_context: Whether to include graph relationships

        Returns:
            List of code contexts
        """
        contexts = []

        for result in semantic_results:
            # Get source code
            source_code = result.source_code
            if not source_code and self.semantic_service.graph_service:
                source_code = self.semantic_service.get_source_code(result.node_id)

            if not source_code:
                continue

            # Build base context
            context = create_code_context(
                source_code=source_code,
                file_path=result.file_path,
                qualified_name=result.qualified_name,
                entity_type=result.type,
            )

            # Enrich with graph context if requested
            if include_graph_context:
                context = self._enrich_context(context, result.node_id)

            contexts.append(context)

        logger.debug(f"Built {len(contexts)} code contexts")
        return contexts

    def _enrich_context(
        self,
        context: CodeContext,
        node_id: int,
    ) -> CodeContext:
        """Enrich context with graph relationships.

        Args:
            context: Base code context
            node_id: Node ID in graph

        Returns:
            Enriched context
        """
        try:
            # Get callers
            if self.config.retrieval.include_callers:
                callers = self.graph_service.fetch_callers(context.qualified_name or "")
                context.callers = [c.qualified_name for c in callers[:5]]

            # Get callees
            if self.config.retrieval.include_callees:
                callees = self.graph_service.fetch_callees(context.qualified_name or "")
                context.callees = [c.qualified_name for c in callees[:5]]

        except Exception as e:
            logger.warning(f"Failed to enrich context: {e}")

        return context

    def _get_related_contexts(self, node_id: int) -> list[CodeContext]:
        """Get contexts for related nodes.

        Args:
            node_id: Node ID

        Returns:
            List of related contexts
        """
        contexts = []

        try:
            related = self.graph_service.fetch_related_nodes(
                node_id,
                relationship_types=["CALLS", "INHERITS", "IMPORTS"],
            )

            for node, rel_type in related[:5]:
                if node.path:
                    source = self.semantic_service.get_source_from_file(
                        node.path,
                        node.start_line or 0,
                        node.end_line or 0,
                    )
                    if source:
                        contexts.append(create_code_context(
                            source_code=source,
                            file_path=node.path,
                            qualified_name=node.qualified_name,
                            entity_type=node.type,
                        ))

        except Exception as e:
            logger.warning(f"Failed to get related contexts: {e}")

        return contexts

    def _node_to_context(self, node: Any) -> CodeContext:
        """Convert graph node to code context.

        Args:
            node: Graph node

        Returns:
            Code context
        """
        source_code = ""

        # Try to get source from graph
        if hasattr(node, "properties") and node.properties:
            source_code = node.properties.get("source_code", "")

        # Fallback to file
        if not source_code and node.path:
            source_code = self.semantic_service.get_source_from_file(
                node.path,
                node.start_line or 0,
                node.end_line or 0,
            ) or ""

        return create_code_context(
            source_code=source_code,
            file_path=node.path,
            qualified_name=node.qualified_name,
            entity_type=node.type,
        )

    def _generate_response(
        self,
        query: str,
        contexts: list[CodeContext],
    ) -> str:
        """Generate response using LLM.

        Args:
            query: User query
            contexts: Retrieved code contexts

        Returns:
            Generated response
        """
        system_prompt, user_prompt = self.prompts.format_rag_query(
            query=query,
            contexts=contexts,
        )

        try:
            response = self.kimi_client.chat_with_messages([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            return response.content
        except Exception as e:
            logger.error(f"Failed to generate response: {e}")
            return f"Error generating response: {e}"

    def _build_sources(self, contexts: list[CodeContext]) -> list[SourceReference]:
        """Build source references from contexts.

        Args:
            contexts: Code contexts

        Returns:
            List of source references
        """
        sources = []
        for ctx in contexts:
            if ctx.qualified_name and ctx.file_path:
                sources.append(SourceReference(
                    name=ctx.qualified_name.split(".")[-1],
                    qualified_name=ctx.qualified_name,
                    file_path=ctx.file_path,
                    entity_type=ctx.entity_type,
                ))
        return sources

    def save_result(
        self,
        result: RAGResult,
        output_path: str | Path | None = None,
    ) -> Path:
        """Save RAG result to markdown file.

        Args:
            result: RAG result to save
            output_path: Output file path (optional)

        Returns:
            Path to saved file
        """
        if output_path is None:
            output_dir = Path(self.config.output.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_query = "".join(c if c.isalnum() else "_" for c in result.query[:50])
            output_path = output_dir / f"rag_result_{safe_query}.md"

        markdown = result.to_markdown(self.markdown_generator)
        return self.markdown_generator.save_document(markdown, output_path)


def create_rag_engine(
    config: RAGConfig | None = None,
    embedder: BaseEmbedder | None = None,
    vector_store: VectorStore | None = None,
    graph_service: GraphServiceProtocol | None = None,
    kimi_client: KimiClient | None = None,
) -> RAGEngine:
    """Factory function to create RAG engine.

    Args:
        config: RAG configuration (from env if not provided)
        embedder: Embedder for semantic search
        vector_store: Vector store for embeddings
        graph_service: Graph service for queries
        kimi_client: Kimi API client

    Returns:
        Configured RAGEngine

    Raises:
        ValueError: If required dependencies are missing
    """
    if config is None:
        config = RAGConfig.from_env()

    config.validate()

    # Create Kimi client if not provided
    if kimi_client is None:
        kimi_client = create_kimi_client(
            api_key=config.moonshot.api_key,
            model=config.moonshot.model,
            base_url=config.moonshot.base_url,
            max_tokens=config.moonshot.max_tokens,
            temperature=config.moonshot.temperature,
        )

    # Create semantic search service if dependencies provided
    if embedder is None or vector_store is None:
        raise ValueError(
            "embedder and vector_store are required for semantic search. "
            "Use create_embedder() and create_vector_store() to create them."
        )

    semantic_service = create_semantic_search_service(
        embedder=embedder,
        vector_store=vector_store,
        graph_service=graph_service,
    )

    # Create graph query service
    if graph_service is None:
        raise ValueError(
            "graph_service is required. "
            "Use MemgraphIngestor or KuzuIngestor as context manager."
        )

    graph_query_service = create_graph_query_service(graph_service)

    return RAGEngine(
        config=config,
        kimi_client=kimi_client,
        semantic_service=semantic_service,
        graph_service=graph_query_service,
    )
