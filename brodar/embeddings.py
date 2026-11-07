"""Gemini Embedding 2 REST client. No credentials in URLs or exceptions."""

from __future__ import annotations

import math

import httpx
from .prompt_files import render

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
        prompt=render('embedding-query.md' if query else 'embedding-document.md',text=text)
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

    async def embed_batch(self,texts: list[str]) -> list[list[float]]:
        """Independent vectors, bounded online batches (not one aggregated vector)."""
        output=[]
        for begin in range(0,len(texts),32):
            batch=texts[begin:begin+32]
            if any(not t.strip() or len(t.encode('utf-8'))>7800 for t in batch):
                raise ValueError('Invalid embedding chunk')
            requests=[{'model':f'models/{MODEL}',
                'content':{'parts':[{'text':render('embedding-document.md',text=t)}]},
                'outputDimensionality':DIMENSIONS} for t in batch]
            try:
                response=await self._client.post(
                    f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents',
                    headers={'x-goog-api-key':self._key},json={'requests':requests})
            except httpx.HTTPError as exc:raise EmbeddingError(f'Batch transport failed ({type(exc).__name__})') from None
            if response.status_code!=200:raise EmbeddingError(f'Batch embedding HTTP {response.status_code}')
            try:
                rows=response.json()['embeddings']
                if len(rows)!=len(batch):raise ValueError('Mismatched batch count')
                for row in rows:
                    vector=[float(x) for x in row['values']]
                    if len(vector)!=DIMENSIONS or not all(math.isfinite(x) for x in vector):raise ValueError('Invalid vector')
                    norm=math.sqrt(sum(x*x for x in vector))
                    if not norm:raise ValueError('Zero vector')
                    output.append([x/norm for x in vector])
            except (KeyError,TypeError,ValueError):raise EmbeddingError('Invalid batch embedding response') from None
        return output

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
