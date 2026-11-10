"""Export the golden eval set: stratified by track x difficulty, balanced across outcomes within each
cell. Real CFPB cases go to a separate file and are never mixed with synthetic ones."""
import json
import random
from pathlib import Path

import pandas as pd

OUT = Path("data/golden")
QUOTA = {("marketplace", "easy"): 32, ("marketplace", "medium"): 43, ("marketplace", "hard"): 33,
         ("fintech", "easy"): 22, ("fintech", "medium"): 29, ("fintech", "hard"): 21}  # 180 = 54/72/54 (30/40/30)
CFPB_PER_CELL = 7  # 2 product groups x 3 labels x 7 = 42


def alloc(quota: int, avail: dict) -> dict:
    """Split quota evenly across outcomes, capped by availability, leftovers redistributed."""
    out = {k: 0 for k in avail}
    while quota > 0:
        open_ = [k for k in avail if out[k] < avail[k]]
        if not open_:
            break
        share = max(1, quota // len(open_))
        for k in open_:
            take = min(share, avail[k] - out[k], quota)
            out[k] += take
            quota -= take
    return out


def _records(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict("records")  # NaN -> null, numpy -> python


def _dump(rows: list[dict], path: Path) -> None:
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def export(con, seed: int) -> None:
    rng = random.Random(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    df = con.execute("""SELECT d.dispute_id AS case_id, d.track, d.scenario_type, d.difficulty, d.is_synthetic,
        d.complaint_text, d.claim_type, d.claimed_amount_usd, d.order_id, d.txn_id, d.customer_id, d.as_of,
        d.injection_attempt, g.outcome AS expected_outcome, g.refund_usd AS expected_refund_usd,
        g.policies AS expected_policies, g.required_tools, g.forbidden_actions
        FROM synthetic.disputes d JOIN synthetic.ground_truth g USING (dispute_id)""").df()
    picked = []
    for (track, diff), quota in QUOTA.items():
        cell = df[(df.track == track) & (df.difficulty == diff)]
        for outcome, k in alloc(quota, cell.expected_outcome.value_counts().to_dict()).items():
            picked += rng.sample(sorted(cell[cell.expected_outcome == outcome].case_id), k)
    rows = _records(df[df.case_id.isin(picked)].sort_values("case_id"))
    for r in rows:
        r["required_tools"] = json.loads(r["required_tools"])
        r["forbidden_actions"] = json.loads(r["forbidden_actions"])
    _dump(rows, OUT / "golden_synthetic.jsonl")
    cf = con.execute("""SELECT 'CFPB-' || complaint_id AS case_id, 'fintech' AS track, 'cfpb_real' AS scenario_type,
        'real' AS difficulty, false AS is_synthetic, narrative AS complaint_text, label AS expected_outcome,
        true AS label_is_proxy, product_group, product, issue, company FROM real.cfpb_complaints""").df()
    sel = pd.concat([cf[(cf.product_group == g) & (cf.expected_outcome == o)].sample(CFPB_PER_CELL, random_state=seed)
                     for g in ("card", "money_transfer") for o in ("approve", "partial", "deny")])
    _dump(_records(sel.sort_values("case_id")), OUT / "golden_cfpb.jsonl")
    print(f"golden: {len(rows)} synthetic + {len(sel)} real CFPB -> {OUT}/")
