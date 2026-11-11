"""Index the policy documents (policies/*.md) in a local Chroma store. Run: make index

Each policy is one chunk: they are short and self-contained, and a retrieved chunk should be a whole rule.
Embeddings are Chroma's default local model (all-MiniLM-L6-v2, ONNX): no API key, no per-call cost.
"""
import re
from pathlib import Path

import chromadb

from disputedesk.config import settings

COLLECTION = "policies"
HEADER = re.compile(r"# (\S+): (.+)\nTrack: (\w+)")


def client(path: str | None = None) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=path or settings.chroma_path,
                                     settings=chromadb.Settings(anonymized_telemetry=False))  # no usage reporting


def load_policies(policy_dir: str = "policies") -> list[dict]:
    docs = []
    for f in sorted(Path(policy_dir).glob("*.md")):
        text = f.read_text()
        m = HEADER.match(text)
        if m is None:
            raise ValueError(f"{f}: expected '# ID: Title' then 'Track: ...'")
        docs.append(dict(id=m[1], title=m[2], track=m[3], text=text))
    return docs


def build(policy_dir: str = "policies", chroma_path: str | None = None) -> int:
    """(Re)build the index from the policy files. Returns the number of policies indexed."""
    col = client(chroma_path).get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    if old := col.get()["ids"]:
        col.delete(ids=old)
    docs = load_policies(policy_dir)
    col.add(ids=[d["id"] for d in docs], documents=[d["text"] for d in docs],
            metadatas=[{"policy_id": d["id"], "title": d["title"], "track": d["track"]} for d in docs])
    return len(docs)


if __name__ == "__main__":
    print(f"indexed {build()} policies into {settings.chroma_path}")
