"""Embedding providers.

The default provider is a fully-offline, deterministic hashing embedding so the
whole system (and its test-suite) runs with zero external calls and stable
results.  Optional OpenAI embeddings can be enabled via configuration for higher
semantic fidelity in production.

All providers return L2-normalised vectors so that dot product == cosine
similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # vectors are pre-normalised, but guard against drift
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        ...


class LocalHashingEmbedder:
    """Deterministic, dependency-free embedding.

    Uses hashed word unigrams + character 3-grams projected into a fixed space
    with signed hashing (the "hashing trick").  This captures lexical overlap
    well enough to drive de-duplication, reinforcement and contradiction
    grouping without any network dependency, and is perfectly reproducible.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _feature(self, token: str, vec: list[float], weight: float = 1.0) -> None:
        h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        idx = h % self.dim
        sign = 1.0 if (h >> 1) & 1 else -1.0
        vec[idx] += sign * weight

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        for tok in tokens:
            self._feature(f"w:{tok}", vec, 1.0)
            padded = f"#{tok}#"
            for i in range(len(padded) - 2):
                self._feature(f"c:{padded[i:i + 3]}", vec, 0.5)
        return _normalise(vec)


class OpenAIEmbedder:
    """OpenAI embeddings (opt-in). Requires ``openai`` and an API key."""

    def __init__(self, model: str = "text-embedding-3-small", dim: int = 256):
        from openai import OpenAI  # lazy import

        self._client = OpenAI()
        self.model = model
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(
            model=self.model, input=text, dimensions=self.dim
        )
        return _normalise(list(resp.data[0].embedding))


def make_embedder(provider: str, model: str, dim: int) -> EmbeddingProvider:
    provider = (provider or "local").lower()
    if provider == "openai":
        try:
            return OpenAIEmbedder(model=model, dim=dim)
        except Exception:  # pragma: no cover - depends on optional dep / creds
            # Fail closed to the offline embedder rather than crashing memory.
            return LocalHashingEmbedder(dim=dim)
    # "anthropic" has no first-party embeddings endpoint; fall back to local.
    return LocalHashingEmbedder(dim=dim)
