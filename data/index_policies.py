"""Chunks the policy documents on their Markdown headers and loads them into Qdrant.

Chunks follow the ## sections, never a fixed size. A section keeps an eligibility rule
together with its conditions, which is what the agent needs to cite it correctly.
The dry run option prints the chunks and stops.

A policy whose front matter says pinned is returned with every search, whatever was asked.
pol_escalation is the one pinned policy: it overrides every other policy, and its wording is
about who is asking and how, which no customer message resembles. A search by meaning found
it for half the tickets that needed it.

The index rebuilds itself when a policy file no longer matches what is stored, so an edit to
a policy never leaves an old copy answering searches.

This module only writes the index. The MCP server's search_policy tool reads it, so the
collection name, the embedding model and the payload fields are a contract between the two.
"""

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

POLICY_DIR = Path(__file__).with_name("policies")
DEFAULT_COLLECTION = "deflect_policies"
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    title: str
    section: str
    applies_to: tuple[str, ...]
    version: int
    text: str
    pinned: bool = False

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"deflect:{self.doc_id}:{self.section}"))

    def payload(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "applies_to": list(self.applies_to),
            "version": self.version,
            "text": self.text,
            "pinned": self.pinned,
        }


def collection_name() -> str:
    return os.getenv("QDRANT_COLLECTION") or DEFAULT_COLLECTION


def split_frontmatter(text: str) -> tuple[dict, str]:
    _, meta, body = text.split("---", 2)
    return yaml.safe_load(meta), body


def chunk_policy(meta: dict, body: str) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = [("Overview", [])]
    for line in body.strip().splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif not line.startswith("# "):
            sections[-1][1].append(line)

    chunks = []
    for heading, lines in sections:
        content = "\n".join(lines).strip()
        if not content:
            continue
        chunks.append(Chunk(
            doc_id=meta["doc_id"],
            title=meta["title"],
            section=heading,
            applies_to=tuple(meta["applies_to"]),
            version=int(meta["version"]),
            text=f"{meta['title']}\n## {heading}\n{content}",
            pinned=bool(meta.get("pinned", False)),
        ))
    return chunks


def load_chunks(directory: Path = POLICY_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(directory.glob("*.md")):
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        chunks.extend(chunk_policy(meta, body))
    return chunks


class FastEmbedder:
    """Local ONNX embeddings. Retrieval stays identical whichever chat model is in use,
    so a provider comparison measures only the chat model."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name)
        self.dim = len(self.embed_query("probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


@lru_cache
def get_embedder():
    return FastEmbedder(os.getenv("DEFLECT_EMBED_MODEL") or DEFAULT_EMBED_MODEL)


@lru_cache
def get_client():
    from qdrant_client import QdrantClient

    url = os.getenv("QDRANT_URL") or "http://localhost:6333"
    if url == ":memory:":
        return QdrantClient(location=":memory:")
    # A hosted Qdrant needs a key. A local one has none, and None means no key is sent.
    return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None, timeout=10)


def index(chunks: list[Chunk], client=None, embedder=None) -> int:
    """Rebuilds the collection from scratch, so running it twice gives the same result."""
    from qdrant_client import models

    client = client or get_client()
    embedder = embedder or get_embedder()
    name = collection_name()

    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=models.VectorParams(size=embedder.dim, distance=models.Distance.COSINE))
    client.create_payload_index(name, "applies_to", models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(name, "pinned", models.PayloadSchemaType.BOOL)

    vectors = embedder.embed_documents([c.text for c in chunks])
    points = [models.PointStruct(id=c.point_id, vector=v, payload=c.payload()) for c, v in zip(chunks, vectors)]
    client.upsert(name, points=points, wait=True)
    return len(points)


def stale(client, name: str, chunks: list[Chunk]) -> bool:
    """Whether what is stored differs from the policy files in any way: a section added or
    removed, a word changed, a policy pinned."""
    if not client.collection_exists(name) or client.count(name).count != len(chunks):
        return True
    stored, _ = client.scroll(name, limit=len(chunks) + 1, with_payload=True)
    held = {str(point.id): point.payload for point in stored}
    return any(held.get(c.point_id) != c.payload() for c in chunks)


def ensure_indexed(client=None, embedder=None) -> None:
    client = client or get_client()
    embedder = embedder or get_embedder()
    chunks = load_chunks()
    name = collection_name()
    if stale(client, name, chunks):
        print(f"The policy index is out of date with the policy files. Rebuilding {name}.", file=sys.stderr)
        index(chunks, client, embedder)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index the policy documents into Qdrant.")
    parser.add_argument("--dry-run", action="store_true", help="print the chunks and stop")
    args = parser.parse_args()

    chunks = load_chunks()
    if args.dry_run:
        for c in chunks:
            print(f"{c.doc_id:<22} {c.section:<45} {len(c.text.split()):>4} words{'  pinned' if c.pinned else ''}")
        print(f"\n{len(chunks)} chunks from {len({c.doc_id for c in chunks})} policies. Dry run, nothing indexed.")
        return 0

    try:
        count = index(chunks)
    except Exception as exc:
        print(f"Indexing failed: {exc}", file=sys.stderr)
        print("Is Qdrant running? Try: docker compose up -d", file=sys.stderr)
        return 1
    print(f"Indexed {count} chunks into {collection_name()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
