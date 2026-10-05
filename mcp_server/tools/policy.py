import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field

from mcp_server.models import Intent, PolicyHit

DEFAULT_COLLECTION = "deflect_policies"
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


class QueryEmbedder:
    """Must be the same model the index was built with, or the scores mean nothing."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name)

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


@lru_cache
def get_embedder():
    return QueryEmbedder(os.getenv("DEFLECT_EMBED_MODEL") or DEFAULT_EMBED_MODEL)


@lru_cache
def get_client():
    from qdrant_client import QdrantClient

    url = os.getenv("QDRANT_URL") or "http://localhost:6333"
    return QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url, timeout=10)


def search_policy(
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    intent: Intent | None = None,
    top_k: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[PolicyHit]:
    """Search the support policies by meaning and return the closest sections.

    Each hit has the doc_id to cite, the policy title, the full text of one section and a
    similarity score. Giving the ticket intent narrows the search to the policies that apply
    to it. Every rule you act on should come from a hit, never from general knowledge. Read only.
    """
    from qdrant_client import models

    query_filter = None
    if intent:
        query_filter = models.Filter(must=[
            models.FieldCondition(key="applies_to", match=models.MatchValue(value=intent)),
        ])
    hits = get_client().query_points(
        os.getenv("QDRANT_COLLECTION") or DEFAULT_COLLECTION,
        query=get_embedder().embed_query(query),
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    ).points
    return [PolicyHit(doc_id=h.payload["doc_id"], title=h.payload["title"], chunk=h.payload["text"], score=h.score)
            for h in hits]
