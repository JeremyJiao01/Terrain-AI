"""Qwen3 Embedder for code semantic embeddings via Alibaba Cloud Bailian API.

This module provides the Qwen3Embedder class for generating code embeddings
using the Qwen3 embedding models via Alibaba Cloud Bailian API.

Required environment variables:
    - DASHSCOPE_API_KEY: Your Alibaba Cloud DashScope API key
    - DASHSCOPE_BASE_URL: API base URL (default: https://dashscope.aliyuncs.com/api/v1)

Example:
    export DASHSCOPE_API_KEY="sk-xxxxxxxx"
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import requests
from loguru import logger


class BaseEmbedder(ABC):
    """Abstract base class for code embedders."""

    @abstractmethod
    def embed_code(self, text: str) -> list[float]:
        """Generate embedding for a single code snippet.

        Args:
            text: Code text to embed

        Returns:
            Embedding vector as list of floats
        """
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple code snippets.

        Args:
            texts: List of code texts to embed

        Returns:
            List of embedding vectors
        """
        ...


class Qwen3Embedder(BaseEmbedder):
    """Qwen3 embedding model wrapper using Alibaba Cloud Bailian API.

    Uses DashScope API to call text-embedding-v4 (Qwen3 Embedding) models.
    No local model download required.

    Args:
        api_key: DashScope API key (or from DASHSCOPE_API_KEY env var)
        model: Model name (default: text-embedding-v4)
        base_url: API base URL
        batch_size: Batch size for embedding generation (max 25 for API)
        max_retries: Maximum number of retries for failed requests
    """

    DEFAULT_MODEL = "text-embedding-v4"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
    DEFAULT_BATCH_SIZE = 25  # API limit
    MAX_BATCH_SIZE = 25
    CODE_RETRIEVAL_TASK = "Given a code query, retrieve relevant code snippets"
    EMBEDDING_DIMENSION = 1536  # text-embedding-v4 output dimension

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DashScope API key required. Set DASHSCOPE_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.model = model
        self.base_url = base_url or os.getenv(
            "DASHSCOPE_BASE_URL", self.DEFAULT_BASE_URL
        )
        self.batch_size = min(batch_size, self.MAX_BATCH_SIZE)
        self.max_retries = max_retries

        # Validate API key format
        if not self.api_key.startswith("sk-"):
            logger.warning("API key format may be invalid. Expected to start with 'sk-'")

        logger.info(f"Initialized Qwen3Embedder with model: {self.model}")

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _make_request(
        self,
        texts: list[str],
        text_type: str = "document",
        dimensions: int | None = None,
    ) -> dict[str, Any]:
        """Make API request to get embeddings.

        Args:
            texts: List of texts to embed
            text_type: Type of text ("document" or "query")
            dimensions: Optional dimension reduction (not supported by all models)

        Returns:
            API response JSON
        """
        url = f"{self.base_url}/services/embeddings/text-embedding/text-embedding"

        payload: dict[str, Any] = {
            "model": self.model,
            "input": {
                "texts": texts,
            },
            "parameters": {
                "text_type": text_type,
            },
        }

        if dimensions is not None:
            payload["parameters"]["dimensions"] = dimensions

        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=60,
                )

                if response.status_code == 200:
                    return response.json()

                # Handle rate limiting
                if response.status_code == 429:
                    import time

                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                # Handle other errors
                error_msg = f"API request failed: {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg += f" - {error_data.get('message', '')}"
                except Exception:
                    error_msg += f" - {response.text[:200]}"

                if attempt < self.max_retries - 1:
                    logger.warning(f"{error_msg}, retrying...")
                    continue

                raise RuntimeError(error_msg)

            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Request timeout, retrying... ({attempt + 1}/{self.max_retries})")
                    continue
                raise RuntimeError("API request timeout after all retries")

            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Request error: {e}, retrying...")
                    continue
                raise RuntimeError(f"API request failed: {e}")

        raise RuntimeError("All retries failed")

    def _extract_embeddings(self, response: dict[str, Any]) -> list[list[float]]:
        """Extract embeddings from API response.

        Args:
            response: API response JSON

        Returns:
            List of embedding vectors
        """
        if "output" not in response or "embeddings" not in response["output"]:
            raise RuntimeError(f"Unexpected API response format: {response.keys()}")

        embeddings = response["output"]["embeddings"]
        return [item["embedding"] for item in embeddings]

    def embed_code(
        self,
        text: str,
        use_instruction: bool = False,
    ) -> list[float]:
        """Generate embedding for a single code snippet.

        Args:
            text: Code text to embed
            use_instruction: Whether to prepend instruction for queries

        Returns:
            Embedding vector as list of floats
        """
        if use_instruction:
            text = self._get_detailed_instruct(self.CODE_RETRIEVAL_TASK, text)

        try:
            response = self._make_request([text], text_type="document")
            embeddings = self._extract_embeddings(response)
            return embeddings[0] if embeddings else []
        except Exception as e:
            logger.error(f"Failed to embed code: {e}")
            raise

    def embed_batch(
        self,
        texts: list[str],
        use_instruction: bool = False,
        show_progress: bool = False,
    ) -> list[list[float]]:
        """Generate embeddings for multiple code snippets.

        Args:
            texts: List of code texts to embed
            use_instruction: Whether to prepend instruction (for queries)
            show_progress: Whether to show progress bar

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if use_instruction:
            texts = [
                self._get_detailed_instruct(self.CODE_RETRIEVAL_TASK, t)
                for t in texts
            ]

        all_embeddings: list[list[float]] = []

        # Process in batches
        iterator = range(0, len(texts), self.batch_size)
        if show_progress:
            try:
                from tqdm import tqdm

                iterator = tqdm(
                    iterator,
                    desc="Generating embeddings",
                    total=(len(texts) + self.batch_size - 1) // self.batch_size,
                )
            except ImportError:
                pass

        for i in iterator:
            batch_texts = texts[i : i + self.batch_size]

            try:
                response = self._make_request(batch_texts, text_type="document")
                batch_embeddings = self._extract_embeddings(response)
                all_embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(f"Failed to embed batch {i // self.batch_size}: {e}")
                # Return partial results on failure
                if all_embeddings:
                    logger.warning("Returning partial results due to failure")
                    return all_embeddings
                raise

        return all_embeddings

    def embed_documents(self, documents: list[str], show_progress: bool = True) -> list[list[float]]:
        """Generate embeddings for documents (code snippets).

        This is for indexing documents (no instruction needed).

        Args:
            documents: List of document texts
            show_progress: Whether to show progress bar

        Returns:
            List of embedding vectors
        """
        return self.embed_batch(
            documents,
            use_instruction=False,
            show_progress=show_progress,
        )

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a query.

        This is for search queries (with instruction for better retrieval).

        Args:
            query: Query text

        Returns:
            Embedding vector as list of floats
        """
        return self.embed_code(query, use_instruction=True)

    def _get_detailed_instruct(self, task_description: str, query: str) -> str:
        """Format query with instruction for better retrieval performance.

        Args:
            task_description: Task description
            query: Query text

        Returns:
            Formatted query with instruction
        """
        return f"Instruct: {task_description}\nQuery: {query}"

    def get_embedding_dimension(self) -> int:
        """Get the embedding dimension for this model.

        Returns:
            Embedding dimension size
        """
        return self.EMBEDDING_DIMENSION

    def health_check(self) -> bool:
        """Check if API is accessible and API key is valid.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Make a simple request
            test_text = "hello"
            self.embed_code(test_text)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


class DummyEmbedder(BaseEmbedder):
    """Dummy embedder for testing without API calls.

    Returns zero vectors of specified dimension.
    """

    def __init__(self, dimension: int = 1536):
        self.dimension = dimension

    def embed_code(self, text: str) -> list[float]:
        """Return zero vector."""
        return [0.0] * self.dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return list of zero vectors."""
        return [[0.0] * self.dimension for _ in texts]


def create_embedder(
    api_key: str | None = None,
    model: str | None = None,
    use_dummy: bool = False,
    **kwargs: Any,
) -> BaseEmbedder:
    """Factory function to create embedder.

    Args:
        api_key: DashScope API key (or from DASHSCOPE_API_KEY env var)
        model: Model name (None for default text-embedding-v4)
        use_dummy: Whether to use dummy embedder for testing
        **kwargs: Additional arguments for Qwen3Embedder

    Returns:
        BaseEmbedder instance
    """
    if use_dummy:
        return DummyEmbedder()

    embedder_kwargs: dict[str, Any] = {}
    if api_key:
        embedder_kwargs["api_key"] = api_key
    if model:
        embedder_kwargs["model"] = model
    embedder_kwargs.update(kwargs)

    return Qwen3Embedder(**embedder_kwargs)


# Keep last_token_pool for backward compatibility (not used in API mode)
def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """Legacy function - not used in API mode. Kept for compatibility."""
    logger.warning("last_token_pool is deprecated when using API mode")
    return last_hidden_states
