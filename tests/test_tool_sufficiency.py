"""Can the tools alone get an agent to the right answer?

For each synthetic dispute, rebuild the facts using only tool outputs (plus what the case itself carries:
claim type, dates, evidence flag), run the policy rules, and compare with the stored ground truth.
It passes only if the tools expose enough, and nothing from after the dispute date is needed.
Default: 8 cases per scenario. Run every dispute with FULL=1.
"""
import os
from datetime import datetime

import pytest
from conftest import needs_db

from disputedesk.synthetic import rules as R

pytestmark = needs_db
PER_SCENARIO = None if os.environ.get("FULL") else 8


def _cases(db):
    limit = "" if PER_SCENARIO is None else f"QUALIFY row_number() OVER (PARTITION BY d.scenario_type ORDER BY hash(d.dispute_id)) <= {PER_SCENARIO}"
    cur = db.execute(f"""SELECT d.dispute_id, d.track, d.scenario_type, d.order_id, d.txn_id, d.customer_id,
        d.claim_type, d.as_of, d.evidence_provided, g.outcome, g.refund_usd
        FROM synthetic.disputes d JOIN synthetic.ground_truth g USING (dispute_id) {limit} ORDER BY d.dispute_id""")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _marketplace(client, c):
    p = {"as_of": c["as_of"].isoformat()}
    o = client.get("/tools/get_order", params={"order_id": c["order_id"], **p}).json()
    ev = client.get("/tools/get_delivery_events", params={"order_id": c["order_id"], **p}).json()
    h = client.get("/tools/get_customer_history", params={"customer_id": c["customer_id"], **p}).json()
    s = (client.get("/tools/get_seller_stats", params={"seller_id": o["seller_id"], **p}).json()
         if o["seller_id"] else {"late_rate": 0.0, "delivered_orders": 0})
    minutes = ev["minutes_late"]
    return R.decide_marketplace(R.MktFacts(
        claim_type=c["claim_type"], status=o["status"], days_late=None if minutes is None else minutes / 1440,
        days_past_estimate=(c["as_of"] - _ts(o["estimated_delivery_ts"])).total_seconds() / 86400,
        total_usd=o["total_usd"], freight_usd=o["freight_usd"], item_price_usd=o["item_price_usd"],
        evidence=bool(c["evidence_provided"]), review_score=o["review_score"],
        seller_late_rate=s["late_rate"], seller_orders=s["delivered_orders"],
        account_age_days=(_ts(o["purchase_ts"]) - _ts(h["signup_ts"])).days, refunds_90d=h["refunds_90d"]))


def _fintech(client, c):
    p = {"as_of": c["as_of"].isoformat()}
    pay = client.get("/tools/get_payment", params={"txn_id": c["txn_id"], **p}).json()
    h = client.get("/tools/get_customer_history", params={"customer_id": c["customer_id"], **p}).json()
    prior = [R.Txn(_ts(t["ts"]), t["amount_usd"], t["merchant"], t["ip"], t["device_id"], t["disputed"])
             for t in h["prior_transactions"] if t["txn_id"] != c["txn_id"]]
    txn = R.Txn(_ts(pay["ts"]), pay["amount_usd"], pay["merchant"], pay["ip"], pay["device_id"])
    return R.decide_fintech(R.FinFacts(c["claim_type"], txn, prior, _ts(pay["card_reported_lost_at"]), h["refunds_90d"]))


def test_tools_are_enough_to_reproduce_the_ground_truth(client, db):
    cases = _cases(db)
    assert len(cases) >= (11 + 14) * 8 if PER_SCENARIO else len(cases) == 1960
    wrong = []
    for c in cases:
        d = (_marketplace if c["track"] == "marketplace" else _fintech)(client, c)
        if d.outcome != c["outcome"] or d.refund_usd != pytest.approx(c["refund_usd"]):
            wrong.append((c["dispute_id"], c["scenario_type"], c["outcome"], d.outcome))
    assert not wrong, f"{len(wrong)} of {len(cases)} disagree, e.g. {wrong[:5]}"
