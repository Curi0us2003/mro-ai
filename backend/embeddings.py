"""
==============================================================
AI Maintenance Voice Copilot
Embeddings Module
--------------------------------------------------------------

Purpose
-------
Single point of access for turning text into vectors.

Ingestion, the agent's knowledge-search tool, and anything else
that needs a vector calls embed_texts() / embed_query() here -
nothing else in the project talks to a model provider directly.

Provider
--------
SAP AI Core / Generative AI Hub, via generative-ai-hub-sdk.
The model is text-embedding-3-large by default, truncated to
EMBEDDING_DIM dimensions so the vectors fit the REAL_VECTOR
column declared in schema.sql.

IMPORTANT
---------
No os.getenv() here - everything comes from backend.config.

Install
-------
    pip install "generative-ai-hub-sdk[all]"
==============================================================
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Sequence

from backend.config import (
    AICORE_EMBEDDING_MODEL,
    AICORE_EMBEDDING_DEPLOYMENT_ID,
    EMBEDDING_DIM,
    EMBEDDING_BATCH_SIZE,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.embeddings")
logger.setLevel(LOG_LEVEL)

_MAX_RETRIES = 4
_RETRY_BACKOFF = 2.0  # seconds, doubled each attempt


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider fails after all retries."""


# ==========================================================
# Provider call
# ==========================================================

def _embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """One request, many texts. Returns vectors in input order."""
    from gen_ai_hub.proxy.native.openai import embeddings

    kwargs: dict = {"input": list(texts)}

    if AICORE_EMBEDDING_DEPLOYMENT_ID:
        kwargs["deployment_id"] = AICORE_EMBEDDING_DEPLOYMENT_ID
    else:
        kwargs["model_name"] = AICORE_EMBEDDING_MODEL

    # Matryoshka truncation - only the -3-* OpenAI models support it.
    if EMBEDDING_DIM and "-3-" in AICORE_EMBEDDING_MODEL:
        kwargs["dimensions"] = EMBEDDING_DIM

    try:
        response = embeddings.create(**kwargs)
    except TypeError:
        # Older SDK builds reject the `dimensions` kwarg outright.
        kwargs.pop("dimensions", None)
        response = embeddings.create(**kwargs)

    # response.data is ordered by `index`, but do not rely on it.
    ordered = sorted(response.data, key=lambda d: d.index)
    return [list(item.embedding) for item in ordered]


def _embed_batch_with_retry(texts: Sequence[str]) -> list[list[float]]:
    delay = _RETRY_BACKOFF
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return _embed_batch(texts)
        except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary
            last_error = exc
            if attempt == _MAX_RETRIES:
                break
            logger.warning(
                "Embedding attempt %d/%d failed (%s). Retrying in %.0fs.",
                attempt, _MAX_RETRIES, exc, delay,
            )
            time.sleep(delay)
            delay *= 2

    raise EmbeddingError(
        f"Embedding failed after {_MAX_RETRIES} attempts: {last_error}"
    ) from last_error


# ==========================================================
# Public API
# ==========================================================

def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    """
    Embed many texts (manual chunks). Returns one vector per input,
    in the same order, batched to keep request sizes sane.
    """
    items = list(texts)
    if not items:
        return []

    vectors: list[list[float]] = []
    for start in range(0, len(items), EMBEDDING_BATCH_SIZE):
        batch = items[start:start + EMBEDDING_BATCH_SIZE]
        vectors.extend(_embed_batch_with_retry(batch))
        logger.info("Embedded %d/%d chunks", len(vectors), len(items))

    sizes = {len(v) for v in vectors}
    if len(sizes) > 1:
        raise EmbeddingError(f"Provider returned mixed vector sizes: {sorted(sizes)}")

    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single technician question for semantic search."""
    return _embed_batch_with_retry([text])[0]


def get_embedding_dimension() -> int:
    """
    Resolve the vector width this configuration actually produces.

    Worth calling once at startup: a mismatch against the
    REAL_VECTOR width in HANA surfaces as a confusing type error
    halfway through an ingest otherwise.
    """
    return len(embed_query("dimension probe"))


if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL)
    print(f"Model     : {AICORE_EMBEDDING_MODEL}")
    print(f"Configured: {EMBEDDING_DIM}")
    print(f"Actual    : {get_embedding_dimension()}")