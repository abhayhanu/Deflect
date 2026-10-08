import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field

from mcp_server.models import Intent, PolicyHit

DEFAULT_COLLECTION = "deflect_policies"
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# The most pinned sections one search may add. Pinning is for the one policy that overrides the rest.
MAX_PINNED = 6


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
    if url == ":memory:":
        return QdrantClient(location=":memory:")
    # A hosted Qdrant needs a key. A local one has none, and None means no key is sent.
    return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None, timeout=10)


def hit(point, pinned: bool = False) -> PolicyHit:
    payload = point.payload
    return PolicyHit(doc_id=payload["doc_id"], title=payload["title"], chunk=payload["text"], score=point.score, pinned=pinned)


def search_policy(
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    intent: Intent | None = None,
    top_k: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[PolicyHit]:
    """Search the support policies by meaning and return the closest sections.

    Each hit has the doc_id to cite, the policy title, the full text of one section and a
    similarity score. Giving the ticket intent narrows the search to the policies that apply
    to it. Every rule you act on should come from a hit, never from general knowledge. Read only.

    After the top_k closest sections come the sections of any pinned policy the search did not
    rank, marked pinned. A pinned policy overrides the others, so it is always in the answer.
    """
    from qdrant_client import models

    collection = os.getenv("QDRANT_COLLECTION") or DEFAULT_COLLECTION
    vector = get_embedder().embed_query(query)
    applies = [models.FieldCondition(key="applies_to", match=models.MatchValue(value=intent))] if intent else []
    always = [*applies, models.FieldCondition(key="pinned", match=models.MatchValue(value=True))]
    client = get_client()

    found = client.query_points(collection, query=vector, query_filter=models.Filter(must=applies) if applies else None,
                                limit=top_k, with_payload=True).points
    pinned = client.query_points(collection, query=vector, query_filter=models.Filter(must=always),
                                 limit=MAX_PINNED, with_payload=True).points
    ranked = {point.id for point in found}
    return [hit(point) for point in found] + [hit(point, pinned=True) for point in pinned if point.id not in ranked]
