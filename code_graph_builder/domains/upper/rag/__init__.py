"""RAG module for code graph-based retrieval and generation.

This module provides RAG (Retrieval-Augmented Generation) capabilities
for code analysis using CAMEL framework and OpenAI-compatible LLM APIs.

Example:
    >>> from code_graph_builder.rag import RAGConfig, create_rag_engine
    >>> from code_graph_builder.rag.camel_agent import CamelAgent
    >>>
    >>> config = RAGConfig.from_env()
    >>> engine = create_rag_engine(config)
    >>> result = engine.query("Explain the authentication flow")
"""

from __future__ import annotations

from .config import (
    MoonshotConfig,
    OutputConfig,
    RAGConfig,
    RetrievalConfig,
)
from .client import (
    ChatResponse,
    LLMClient,
    create_llm_client,
)
from .markdown_generator import (
    AnalysisResult,
    MarkdownGenerator,
    SourceReference,
)
from .prompt_templates import (
    CodeAnalysisPrompts,
    CodeContext,
    RAGPrompts,
    create_code_context,
)
from .rag_engine import (
    RAGEngine,
    RAGResult,
    create_rag_engine,
)

__all__ = [
    # Config
    "RAGConfig",
    "MoonshotConfig",
    "RetrievalConfig",
    "OutputConfig",
    # Engine
    "RAGEngine",
    "RAGResult",
    "create_rag_engine",
    # LLM Client
    "LLMClient",
    "ChatResponse",
    "create_llm_client",
    # Prompts
    "CodeAnalysisPrompts",
    "RAGPrompts",
    "CodeContext",
    "create_code_context",
    # Markdown
    "MarkdownGenerator",
    "AnalysisResult",
    "SourceReference",
]
