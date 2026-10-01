import hashlib
import math
import re

import pytest
from qdrant_client import QdrantClient

from data.index_policies import collection_name, ensure_indexed, load_chunks


class HashEmbedder:
    """A tiny bag of words embedder so the tests need no model download."""

    dim = 256

    def embed_query(self, text):
        vector = [0.0] * self.dim
        for word in re.findall(r"[a-z]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


@pytest.fixture(scope="module")
def indexed():
    client, embedder = QdrantClient(location=":memory:"), HashEmbedder()
    ensure_indexed(client, embedder)
    return client, embedder


def test_chunks_follow_headers():
    chunks = load_chunks()
    assert len({c.doc_id for c in chunks}) == 8
    for c in chunks:
        assert c.text.count("\n## ") == 1
        assert c.applies_to
    assert len({c.point_id for c in chunks}) == len(chunks)


def test_a_rule_stays_with_its_condition():
    lost = {c.section: c.text for c in load_chunks() if c.doc_id == "pol_lost_transit"}
    rules = lost["Refund rules for lost shipments"]
    assert "Rs 5,000" in rules and "90 day" in rules


def test_indexing_twice_gives_the_same_collection(indexed):
    client, embedder = indexed
    ensure_indexed(client, embedder)
    assert client.count(collection_name()).count == len(load_chunks())
