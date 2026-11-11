from pathlib import Path

from conftest import needs_db
from fastapi.testclient import TestClient

from disputedesk.rag import index, search
from disputedesk.rag.eval import evaluate
from disputedesk.tools.app import create_app


def test_index_has_every_policy_file(chroma_path):
    n = len(list(Path("policies").glob("*.md")))
    assert n == 10 and index.client(chroma_path).get_collection(index.COLLECTION).count() == n


def test_rebuild_does_not_duplicate(tmp_path):
    path = str(tmp_path / "c")
    assert index.build(chroma_path=path) == index.build(chroma_path=path) == 10
    assert index.client(path).get_collection(index.COLLECTION).count() == 10


def test_obvious_queries_find_the_right_policy(chroma_path):
    # Related policies (MKT-01 late tiers, MKT-05 new-customer shipping refund) rank within a few hundredths of
    # each other, so top-1 is unreliable between them; the right policy must be in the top 3.
    top3 = search.search_policy("order arrived days late, refund the shipping fee", "marketplace", 3, chroma_path)
    assert "MKT-01" in {h.policy_id for h in top3}
    top1 = search.search_policy("charged twice for the same amount by the same merchant", "fintech", 1, chroma_path)
    assert top1[0].policy_id == "FIN-02"


@needs_db
def test_missing_index_is_a_clear_503(tmp_path):
    client = TestClient(create_app(actions_path=str(tmp_path / "a.db"), chroma_path=str(tmp_path / "empty")))
    r = client.get("/tools/search_policy", params={"query": "late"})
    assert r.status_code == 503 and "make index" in r.json()["detail"]


@needs_db
def test_retrieval_quality_does_not_regress(chroma_path):
    # Measured when written: structured query 0.92 hit@3, raw complaint 0.83. Thresholds sit a little below.
    res = evaluate(k=3, chroma_path=chroma_path)
    assert res["structured"]["hit"] >= 0.88 and res["complaint"]["hit"] >= 0.78
