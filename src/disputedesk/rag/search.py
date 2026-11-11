"""search_policy: find the policies most relevant to a query, filtered to the case's track."""
from functools import lru_cache

import chromadb.errors

from disputedesk.rag.index import COLLECTION, client
from disputedesk.tools.models import PolicyHit


class IndexMissing(RuntimeError):
    pass


@lru_cache(maxsize=8)
def _collection(chroma_path: str | None):
    try:
        return client(chroma_path).get_collection(COLLECTION)
    except chromadb.errors.NotFoundError as e:
        raise IndexMissing("policy index not built: run `make index`") from e


# shortcut: cached per process and never invalidated, so restart the service after re-running `make index`
@lru_cache(maxsize=1024)
def search_policy(query: str, track: str | None = None, k: int = 3,
                  chroma_path: str | None = None) -> tuple[PolicyHit, ...]:
    """Top-k policies for the query. A track ('marketplace' or 'fintech') also returns the general policies."""
    where = None if track is None else {"track": {"$in": [track, "general"]}}
    r = _collection(chroma_path).query(query_texts=[query], n_results=k, where=where)
    return tuple(PolicyHit(policy_id=m["policy_id"], track=m["track"], title=m["title"], text=doc, distance=dist)
                 for m, doc, dist in zip(r["metadatas"][0], r["documents"][0], r["distances"][0], strict=True))
