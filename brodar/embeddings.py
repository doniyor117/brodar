"""Gemini Embedding 2 REST client. No credentials in URLs or exceptions."""

from __future__ import annotations

import math

import httpx

MODEL = 'gemini-embedding-2'
DIMENSIONS = 768


class EmbeddingError(RuntimeError):
    pass


class GeminiEmbeddings:
    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None):
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=45)
        self._owns_client = client is None
        self.model = MODEL
        self.dimensions = DIMENSIONS

    async def embed(self, text: str, *, query: bool = False) -> list[float]:
        if not text.strip() or len(text.encode('utf-8')) > 7800:
            raise ValueError('Embed one nonempty chunk of at most 7800 UTF-8 bytes')
        prompt = (f'task: search result | query: {text}' if query
                  else f'title: conversation context | text: {text}')
        try:
            response = await self._client.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent',
                headers={'x-goog-api-key': self._key},
                json={'model': f'models/{MODEL}', 'content': {'parts': [{'text': prompt}]},
                      'outputDimensionality': DIMENSIONS},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f'Embedding transport failed ({type(exc).__name__})') from None
        if response.status_code != 200:
            # Do not forward vendor error text: it can include input or secrets.
            raise EmbeddingError(f'Embedding HTTP {response.status_code}')
        try:
            vector = response.json()['embedding']['values']
            if len(vector) != DIMENSIONS or not all(math.isfinite(float(x)) for x in vector):
                raise ValueError('invalid vector')
            norm = math.sqrt(sum(float(x) ** 2 for x in vector))
            if norm == 0:
                raise ValueError('zero vector')
            return [float(x)/norm for x in vector]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f'Invalid embedding response ({type(exc).__name__})') from None

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
