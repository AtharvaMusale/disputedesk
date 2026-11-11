"""Retrieval quality on the golden set, with no LLM involved. Run: make rag-eval

For each synthetic golden case, search with a query and check whether the policies that decide the case
(`expected_policies`) come back in the top k.
  hit@k    = at least one deciding policy retrieved
  recall@k = share of the deciding policies retrieved
Two query styles: the raw complaint text, and a structured query like the one the intake agent would build.
"""
import json
from pathlib import Path

from disputedesk.rag.search import search_policy

GOLDEN = "data/golden/golden_synthetic.jsonl"


def _queries(r: dict) -> dict[str, str]:
    return {"complaint": r["complaint_text"],
            "structured": f"{r['claim_type'].replace('_', ' ')} claim, {r['track']} dispute, refund policy"}


def evaluate(golden: str = GOLDEN, k: int = 3, chroma_path: str | None = None) -> dict:
    rows = [json.loads(line) for line in Path(golden).read_text().splitlines()]
    out = {}
    for style in ("complaint", "structured"):
        hits = recalls = 0.0
        for r in rows:
            expected = set(r["expected_policies"].split(","))
            got = {h.policy_id for h in search_policy(_queries(r)[style], r["track"], k, chroma_path)}
            hits += bool(expected & got)
            recalls += len(expected & got) / len(expected)
        out[style] = {"hit": hits / len(rows), "recall": recalls / len(rows)}
    return {"k": k, "cases": len(rows), **out}


if __name__ == "__main__":
    for k in (1, 3, 5):
        res = evaluate(k=k)
        print(f"k={k} ({res['cases']} cases): " + "  ".join(
            f"{s}: hit {res[s]['hit']:.2f} recall {res[s]['recall']:.2f}" for s in ("complaint", "structured")))
