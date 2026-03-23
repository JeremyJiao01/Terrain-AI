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
import warnings
from abc import ABC, abstractmethod
from typing import Any

import requests
from loguru import logger

# Suppress SSL verification warnings when verify=False is used (e.g. third-party proxy)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


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

    @abstractmethod
    def get_embedding_dimension(self) -> int:
        """Return the embedding vector dimension."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a search query.

        Subclasses may override to add task instructions for better retrieval.
        """
        return self.embed_code(query)

    def embed_documents(self, documents: list[str], show_progress: bool = True) -> list[list[float]]:
        """Generate embeddings for documents (code snippets for indexing)."""
        return self.embed_batch(documents)


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
        url = f"{self.base_url}/embeddings"

        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
        }

        if dimensions is not None:
            payload["dimensions"] = dimensions

        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=60,
                    verify=False,
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
        """Extract embeddings from API response (OpenAI-compatible format).

        Expects ``{"data": [{"embedding": [...], "index": 0}, ...]}``

        Args:
            response: API response JSON

        Returns:
            List of embedding vectors
        """
        if "data" not in response:
            raise RuntimeError(f"Unexpected API response format: {list(response.keys())}")

        sorted_items = sorted(response["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in sorted_items]

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
                batch_num = i // self.batch_size + 1
                total_batches = (len(texts) + self.batch_size - 1) // self.batch_size
                logger.error(
                    f"Embedding batch {batch_num}/{total_batches} failed: {e}"
                )
                raise RuntimeError(
                    f"Embedding API call failed at batch {batch_num}/{total_batches}: {e}. "
                    f"Successfully embedded {len(all_embeddings)}/{len(texts)} texts before failure."
                ) from e

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


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI-compatible embedding client.

    Works with OpenAI, Azure OpenAI, and any API implementing the
    ``/v1/embeddings`` endpoint (e.g. local ollama, vLLM, LiteLLM).

    Env vars (fallback order):
        EMBEDDING_API_KEY / OPENAI_API_KEY / LLM_API_KEY
        EMBEDDING_BASE_URL / OPENAI_BASE_URL / LLM_BASE_URL  (default: https://api.openai.com/v1)
        EMBEDDING_MODEL  (default: text-embedding-3-small)
    """

    DEFAULT_MODEL = "text-embedding-3-small"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    # text-embedding-3-small = 1536, text-embedding-3-large = 3072
    _KNOWN_DIMS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        batch_size: int = 20,
        max_retries: int = 3,
        dimension: int | None = None,
    ):
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Set EMBEDDING_API_KEY, OPENAI_API_KEY, "
                "or LLM_API_KEY environment variable."
            )

        self.model = model or os.getenv("EMBEDDING_MODEL", self.DEFAULT_MODEL)
        self.base_url = (
            base_url
            or os.getenv("EMBEDDING_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._dimension = dimension or self._KNOWN_DIMS.get(self.model, 1536)

        logger.info(f"Initialized OpenAIEmbedder with model: {self.model}")

    def _make_request(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
        }

        for attempt in range(self.max_retries):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=60)

                if response.status_code == 200:
                    data = response.json()
                    sorted_items = sorted(data["data"], key=lambda x: x["index"])
                    return [item["embedding"] for item in sorted_items]

                if response.status_code == 429:
                    import time
                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                error_msg = f"OpenAI embeddings API error: {response.status_code}"
                try:
                    err = response.json()
                    error_msg += f" - {err.get('error', {}).get('message', response.text[:200])}"
                except Exception:
                    error_msg += f" - {response.text[:200]}"

                if attempt < self.max_retries - 1:
                    logger.warning(f"{error_msg}, retrying...")
                    continue
                raise RuntimeError(error_msg)

            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Request timeout, retrying ({attempt + 1}/{self.max_retries})...")
                    continue
                raise RuntimeError("OpenAI embeddings API timeout after all retries")
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Request error: {e}, retrying...")
                    continue
                raise RuntimeError(f"OpenAI embeddings API request failed: {e}")

        raise RuntimeError("All retries failed")

    def embed_code(self, text: str) -> list[float]:
        results = self._make_request([text])
        return results[0] if results else []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            all_embeddings.extend(self._make_request(batch))
        return all_embeddings

    def get_embedding_dimension(self) -> int:
        return self._dimension


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

    def get_embedding_dimension(self) -> int:
        return self.dimension


def create_embedder(
    api_key: str | None = None,
    model: str | None = None,
    use_dummy: bool = False,
    provider: str | None = None,
    **kwargs: Any,
) -> BaseEmbedder:
    """Factory function to create an embedder.

    Provider detection order:
        1. Explicit ``provider`` argument (``"qwen3"``, ``"openai"``, ``"dummy"``).
        2. ``EMBEDDING_PROVIDER`` env var.
        3. Auto-detect: if ``DASHSCOPE_API_KEY`` is set → Qwen3,
           elif ``EMBEDDING_API_KEY`` or ``OPENAI_API_KEY`` or ``LLM_API_KEY`` → OpenAI-compatible,
           else → DummyEmbedder (with a warning).

    Args:
        api_key: API key override (passed to chosen embedder).
        model: Model name override.
        use_dummy: Force dummy embedder (for tests).
        provider: Explicit provider name.
        **kwargs: Extra arguments forwarded to the embedder constructor.

    Returns:
        BaseEmbedder instance.
    """
    if use_dummy:
        return DummyEmbedder()

    chosen = (provider or os.getenv("EMBEDDING_PROVIDER", "")).lower()

    if not chosen:
        # Auto-detect
        if os.getenv("DASHSCOPE_API_KEY"):
            chosen = "qwen3"
        elif os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"):
            chosen = "openai"
        else:
            logger.warning("No embedding API key found. Using DummyEmbedder (zero vectors).")
            return DummyEmbedder()

    embedder_kwargs: dict[str, Any] = {}
    if api_key:
        embedder_kwargs["api_key"] = api_key
    if model:
        embedder_kwargs["model"] = model
    embedder_kwargs.update(kwargs)

    if chosen == "qwen3":
        return Qwen3Embedder(**embedder_kwargs)
    elif chosen == "openai":
        return OpenAIEmbedder(**embedder_kwargs)
    else:
        raise ValueError(f"Unknown embedding provider: {chosen!r}. Use 'qwen3', 'openai', or 'dummy'.")


# Keep last_token_pool for backward compatibility (not used in API mode)
def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """Legacy function - not used in API mode. Kept for compatibility."""
    logger.warning("last_token_pool is deprecated when using API mode")
    return last_hidden_states
