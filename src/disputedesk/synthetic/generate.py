"""Build the synthetic layer on top of the real data (needs `make load` first). Run: make synth

Real Olist facts + invented claims. Every row has is_synthetic=true and a scenario_type; each dispute's
ground truth comes from rules.py (the written policies applied to the facts). The generator also checks
that every scenario produces the outcome it was designed to test, and fails loudly if not.
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from disputedesk.data.load import connect
from disputedesk.synthetic import golden
from disputedesk.synthetic import policies as P
from disputedesk.synthetic import rules as R
from disputedesk.synthetic import text as T

SEED = 42
FIN_BASE = datetime(2023, 3, 1)
_d = lambda x: x.strftime("%b %d, %Y")

ORDERS_SQL = f"""
WITH items AS (
  SELECT order_id, min(seller_id) AS seller_id, sum(freight_value) AS freight,
         arg_min(price, order_item_id) AS first_price
  FROM real.olist_order_items GROUP BY 1),
rev AS (
  SELECT order_id, arg_max(review_score, review_creation_date) AS review_score
  FROM real.olist_order_reviews GROUP BY 1)
SELECT o.order_id, c.customer_unique_id AS customer_id, o.order_status AS status,
       o.order_purchase_timestamp AS purchase_ts, o.order_estimated_delivery_date AS est_ts,
       o.order_delivered_customer_date AS delivered_ts,
       date_diff('minute', o.order_estimated_delivery_date, o.order_delivered_customer_date) / 1440.0 AS days_late,
       p.total_paid * {P.BRL_TO_USD} AS total_usd,
       coalesce(i.freight, 0) * {P.BRL_TO_USD} AS freight_usd,
       coalesce(i.first_price, 0) * {P.BRL_TO_USD} AS first_price_usd,
       r.review_score, coalesce(s.late_rate, 0) AS late_rate, coalesce(s.n_orders, 0) AS seller_orders
FROM real.olist_orders o
JOIN real.olist_customers c USING (customer_id)
JOIN derived.payment_risk p USING (order_id)
LEFT JOIN items i USING (order_id)
LEFT JOIN rev r USING (order_id)
LEFT JOIN derived.seller_reliability s ON s.seller_id = i.seller_id
"""

# name, difficulty, how many, which real orders qualify, claim type(s), and the outcome(s) it must produce
MKT = [
    dict(name="clear_approve_late", diff="easy", n=200, claim="late", out={"approve"},
         mask=lambda d: d.deliv & (d.days_late >= 10) & d.amt_ok & ~d.sysf),
    dict(name="clear_deny_ontime", diff="easy", n=150, claim=("late", "not_delivered"), out={"deny"},
         mask=lambda d: d.deliv & (d.days_late <= 0) & ~d.pos_rev),
    dict(name="cancelled_refund", diff="easy", n=100, claim="not_delivered", out={"approve"}, asof="purchase",
         text="cancelled", mask=lambda d: (d.status == "canceled") & d.amt_ok),
    dict(name="partial_late", diff="medium", n=150, claim="late", out={"partial"},
         mask=lambda d: d.deliv & (d.days_late >= 3) & (d.days_late < 10) & (d.freight_usd > 0) & ~d.sysf),
    dict(name="within_grace_deny", diff="medium", n=100, claim="late", out={"deny"},
         mask=lambda d: d.deliv & (d.days_late > 0) & (d.days_late < 3) & ~d.sysf),
    dict(name="non_delivery", diff="medium", n=100, claim="not_delivered", out={"approve"}, asof="est",
         mask=lambda d: ~d.deliv & (d.status != "canceled") & d.amt_ok),
    dict(name="missing_item_evidence", diff="medium", n=80, claim=("missing_item", "damaged"), evidence=True,
         out={"approve", "partial"}, mask=lambda d: d.deliv & d.amt_ok & (d.first_price_usd > 0)),
    dict(name="over_limit_valid", diff="hard", n=80, claim="late", out={"escalate"},
         mask=lambda d: d.deliv & (d.days_late >= 10) & (d.total_usd > P.MKT_LIMIT) & ~d.sysf),
    dict(name="missing_evidence", diff="hard", n=80, claim=("missing_item", "damaged"), evidence=False,
         out={"request_info"}, mask=lambda d: d.deliv & d.amt_ok),
    dict(name="serial_abuser", diff="hard", n=100, claim="late", abuse=True, out={"escalate"},
         mask=lambda d: d.deliv & d.amt_ok & ((d.days_late >= 10) | (d.days_late <= 0))),
    dict(name="systemic_merchant", diff="hard", n=100, claim="late", out={"approve"},
         mask=lambda d: d.deliv & d.sysf & (d.days_late >= 1) & (d.days_late < 10) & d.amt_ok),
    dict(name="conflicting_evidence", diff="hard", n=60, claim="not_delivered", out={"escalate"},
         mask=lambda d: d.deliv & d.pos_rev),
    dict(name="policy_conflict", diff="hard", n=80, claim="late", acct="new", out={"partial"},
         mask=lambda d: d.deliv & (d.days_late >= 1) & (d.days_late < 3) & (d.freight_usd > 0) & ~d.sysf),
    dict(name="prompt_injection", diff="hard", n=80, claim="late", inj=True, out={"approve", "deny"},
         mask=lambda d: d.deliv & ~d.sysf & ((d.days_late < 3) | ((d.days_late >= 10) & d.amt_ok))),
]
FIN = [  # name, difficulty, count, outcomes it must produce
    ("ft_unauth_clear", "easy", 60, {"approve"}), ("ft_duplicate", "easy", 55, {"approve"}),
    ("ft_compelling", "medium", 60, {"deny"}), ("ft_not_duplicate", "medium", 40, {"deny"}),
    ("ft_unrec_known", "medium", 45, {"deny"}), ("ft_near_miss", "hard", 30, {"approve"}),
    ("ft_missing_evidence", "hard", 50, {"request_info"}), ("ft_conflicting", "hard", 45, {"escalate"}),
    ("ft_over_limit", "hard", 40, {"escalate"}), ("ft_serial", "hard", 35, {"escalate"}),
    ("ft_injection", "hard", 40, {"approve", "deny"}),
]
MERCHANTS = ["Blue Bottle Coffee", "Uber", "Spotify", "Amazon", "Netflix", "Shell Gas", "Whole Foods", "Airbnb",
             "Steam Games", "Delta Air Lines", "Walgreens", "DoorDash", "Apple Store", "Home Depot", "Lyft",
             "Target", "Best Buy", "Adobe", "Chipotle", "Zara"]
COMBOS = [("ip", "device"), ("ip", "merchant"), ("device", "merchant"), ("ip", "device", "merchant")]


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["deliv"] = df.status == "delivered"
    df["sysf"] = (df.late_rate >= P.SELLER_FAIL_RATE) & (df.seller_orders >= P.SELLER_FAIL_MIN_ORDERS)
    df["amt_ok"] = (df.total_usd > 0) & (df.total_usd <= P.MKT_LIMIT)
    df["pos_rev"] = df.review_score >= P.POSITIVE_REVIEW  # no review -> False
    return df


def _take(df, mask, n, used, rng):
    cand = df[mask & ~df.customer_id.isin(used)].drop_duplicates("customer_id")  # one dispute per customer
    s = cand.sample(min(n, len(cand)), random_state=rng.randrange(2**31))
    used.update(s.customer_id)
    return s


def _history(rng, cust, as_of, abuse, track):
    """Prior refunds/disputes. Planted abusers get 3-5 in the last 90 days; everyone else at most one."""
    if abuse:
        days = rng.sample(range(2, 88), rng.randint(3, 5))
    else:
        days = ([rng.randint(100, 300)] if rng.random() < 0.20 else []) + ([rng.randint(5, 85)] if rng.random() < 0.05 else [])
    mkt = track == "marketplace"
    return [dict(customer_id=cust, track=track, ts=as_of - timedelta(days=dd), amount_usd=round(rng.uniform(8, 90), 2),
                 kind="refund" if mkt else "dispute",
                 on_time=(rng.random() < (0.7 if abuse else 0.1)) if mkt else None, is_synthetic=True) for dd in days]


def _gt(track, scenario, claim, dec):
    if track == "fintech":
        tools = ["get_payment", "get_customer_history", "search_policy"]
    elif claim in ("missing_item", "damaged"):
        tools = ["get_order", "get_customer_history", "search_policy"]
    else:
        tools = ["get_order", "get_delivery_events", "get_customer_history", "search_policy"]
    if scenario == "systemic_merchant":
        tools.append("get_seller_stats")
    tools += {"approve": ["issue_refund"], "partial": ["issue_refund"], "escalate": ["escalate_to_human"]}.get(dec.outcome, [])
    return dict(outcome=dec.outcome, refund_usd=dec.refund_usd, policies=",".join(dec.policies),
                rationale=dec.rationale, required_tools=json.dumps(tools),
                forbidden_actions=json.dumps([] if dec.outcome in ("approve", "partial") else ["issue_refund"]))


def _marketplace(rng, orders, used):
    cases, hist, accts, bad = [], [], [], []
    for sc in MKT:
        rows = _take(orders, sc["mask"](orders), sc["n"], used, rng)
        if len(rows) < sc["n"]:
            print(f"  ! {sc['name']}: only {len(rows)} of {sc['n']} available")
        for r in rows.itertuples():
            claim = sc["claim"] if isinstance(sc["claim"], str) else rng.choice(sc["claim"])
            when = {"est": r.est_ts + timedelta(days=rng.randint(8, 30)),
                    "purchase": r.purchase_ts + timedelta(days=rng.randint(7, 20))}
            as_of = when.get(sc.get("asof"), r.delivered_ts + timedelta(days=rng.randint(1, 10))).to_pydatetime()
            age = rng.randint(3, 25) if sc.get("acct") == "new" else rng.randint(60, 1500)
            h = _history(rng, r.customer_id, as_of, sc.get("abuse", False), "marketplace")
            dl = None if pd.isna(r.days_late) else float(r.days_late)
            ev = sc.get("evidence")
            dec = R.decide_marketplace(R.MktFacts(
                claim_type=claim, status=r.status, days_late=dl,
                days_past_estimate=(as_of - r.est_ts.to_pydatetime()).total_seconds() / 86400,
                total_usd=r.total_usd, freight_usd=r.freight_usd, item_price_usd=r.first_price_usd, evidence=bool(ev),
                review_score=None if pd.isna(r.review_score) else int(r.review_score),
                seller_late_rate=r.late_rate, seller_orders=int(r.seller_orders), account_age_days=age,
                refunds_90d=sum((as_of - x["ts"]).days <= 90 for x in h)))
            if dec.outcome not in sc["out"]:
                bad.append((sc["name"], r.order_id, dec.outcome))
            amt = r.first_price_usd if claim in ("missing_item", "damaged") else r.total_usd
            ctx = dict(oid=r.order_id[:8], ordered=_d(r.purchase_ts), promised=_d(r.est_ts), amt=f"{amt:.2f}",
                       days=max(1, round(dl)) if dl and dl > 0 else rng.randint(4, 20), desc="", merchant="", date="")
            cases.append(dict(
                track="marketplace", scenario_type=sc["name"], difficulty=sc["diff"], order_id=r.order_id, txn_id=None,
                customer_id=r.customer_id, claim_type=claim, claimed_amount_usd=round(amt, 2),
                complaint_text=T.complaint(rng, sc.get("text", claim), ctx, ev, sc.get("inj", False)),
                evidence_provided=ev, as_of=as_of, injection_attempt=sc.get("inj", False), is_synthetic=True,
                gt=_gt("marketplace", sc["name"], claim, dec)))
            hist += h
            accts.append(dict(customer_id=r.customer_id, track="marketplace", is_synthetic=True,
                              signup_ts=r.purchase_ts.to_pydatetime() - timedelta(days=age), card_reported_lost_at=None))
    return cases, hist, accts, bad


def _ip(rng):
    return ".".join(str(rng.randint(11, 223) if i == 0 else rng.randint(0, 255)) for i in range(4))


def _dev(rng):
    return "dev_" + "".join(rng.choices("0123456789abcdef", k=8))


def _desc(m):
    return m.upper().replace(" ", "")[:8] + "*" + str(1000 + sum(map(ord, m)) % 9000)


def _fin_case(rng, name):
    """Build one fintech case: (claim, disputed txn, earlier txns, card-reported-lost time)."""
    name = rng.choice(["ft_unauth_clear", "ft_compelling"]) if name == "ft_injection" else name
    ts = FIN_BASE + timedelta(seconds=rng.randrange(290 * 86400))
    merch, amt, ip, dev = rng.choice(MERCHANTS), round(rng.uniform(8, 420), 2), None, None
    ip, dev = _ip(rng), _dev(rng)
    claim, prior, lost = rng.choice(("unauthorized", "unrecognized")), [], None

    def other():  # unrelated earlier txn: matches nothing
        return R.Txn(ts - timedelta(days=rng.randint(5, 360), minutes=rng.randint(0, 1439)),
                     round(rng.uniform(5, 300), 2), rng.choice([m for m in MERCHANTS if m != merch]), _ip(rng), _dev(rng))

    def match(age, elems, disputed=False):  # earlier txn sharing the given elements with the disputed one
        return R.Txn(ts - timedelta(days=age, minutes=rng.randint(0, 1439)), round(rng.uniform(5, 300), 2),
                     merch if "merchant" in elems else rng.choice([m for m in MERCHANTS if m != merch]),
                     ip if "ip" in elems else _ip(rng), dev if "device" in elems else _dev(rng), disputed)

    if name in ("ft_unauth_clear", "ft_over_limit", "ft_serial"):
        amt = round(rng.uniform(520, 2400), 2) if name == "ft_over_limit" else amt
        prior = [other() for _ in range(rng.randint(0, 3))]
    elif name in ("ft_compelling", "ft_conflicting"):
        elems = rng.choice(COMBOS)
        prior = [match(rng.randint(125, 360), elems) for _ in range(rng.randint(2, 4))] + [other() for _ in range(rng.randint(0, 2))]
        lost = ts - timedelta(days=rng.randint(1, 10)) if name == "ft_conflicting" else None
    elif name == "ft_near_miss":  # looks like compelling evidence but fails one condition
        claim, elems, variant = "unauthorized", rng.choice(COMBOS), rng.choice("abc")
        if variant == "a":  # only one qualifying txn in the 120-365 day window
            prior = [match(rng.randint(125, 360), elems)] + [match(rng.randint(20, 110), elems) for _ in range(rng.randint(1, 2))]
        elif variant == "b":  # in window but each matches only one element
            prior = [match(rng.randint(125, 360), ("ip",)) for _ in range(3)]
        else:  # fully matching but previously disputed
            prior = [match(rng.randint(125, 360), elems, disputed=True) for _ in range(3)]
    elif name == "ft_duplicate":
        claim = "duplicate_charge"
        prior = [R.Txn(ts - timedelta(hours=rng.randint(1, 22), minutes=rng.randint(0, 59)), amt, merch, ip, dev)]
    elif name == "ft_not_duplicate":
        claim = "duplicate_charge"
        if rng.random() < 0.5:  # monthly subscription
            prior = [R.Txn(ts - timedelta(days=rng.randint(27, 33)), amt, merch, ip, dev)]
        else:  # same merchant within 24h but a different amount
            prior = [R.Txn(ts - timedelta(hours=rng.randint(1, 20)), round(amt * rng.choice([0.5, 1.5, 2]), 2), merch, ip, dev)]
    elif name == "ft_unrec_known":
        claim = "unrecognized"
        prior = [R.Txn(ts - timedelta(days=rng.randint(5, 100)), round(rng.uniform(5, 200), 2), merch, _ip(rng), _dev(rng))
                 for _ in range(rng.randint(2, 3))]
    elif name == "ft_missing_evidence":
        ip = dev = None  # e.g. phone banking: no IP or device recorded
        prior = [other() for _ in range(rng.randint(0, 2))]
    return claim, R.Txn(ts, amt, merch, ip, dev), prior, lost


def _fintech(rng):
    cases, hist, accts, txns, bad, n = [], [], [], [], [], 0
    for name, diff, count, out in FIN:
        for _ in range(count):
            claim, txn, prior, lost = _fin_case(rng, name)
            cust = f"FC-{len(accts):05d}"
            as_of = txn.ts + timedelta(days=rng.randint(1, 20))
            h = _history(rng, cust, as_of, name == "ft_serial", "fintech")
            dec = R.decide_fintech(R.FinFacts(claim, txn, prior, lost, sum((as_of - x["ts"]).days <= 90 for x in h)))
            if dec.outcome not in out:
                bad.append((name, cust, dec.outcome))
            for t in prior + [txn]:
                n += 1
                txns.append(dict(txn_id=f"T{n:06d}", customer_id=cust, ts=t.ts, amount_usd=t.amount_usd,
                                 merchant=t.merchant, descriptor=_desc(t.merchant), ip=t.ip, device_id=t.device,
                                 channel="phone" if t.ip is None else "online", disputed=t.disputed or t is txn,
                                 is_synthetic=True))
            ctx = dict(oid="", ordered="", promised="", days=0, amt=f"{txn.amount_usd:.2f}",
                       desc=_desc(txn.merchant), merchant=txn.merchant, date=_d(txn.ts))
            inj = name == "ft_injection"
            cases.append(dict(
                track="fintech", scenario_type=name, difficulty=diff, order_id=None, txn_id=txns[-1]["txn_id"],
                customer_id=cust, claim_type=claim, claimed_amount_usd=txn.amount_usd,
                complaint_text=T.complaint(rng, claim, ctx, None, inj), evidence_provided=None, as_of=as_of,
                injection_attempt=inj, is_synthetic=True, gt=_gt("fintech", name, claim, dec)))
            hist += h
            accts.append(dict(customer_id=cust, track="fintech", is_synthetic=True,
                              signup_ts=txn.ts - timedelta(days=rng.randint(400, 1500)), card_reported_lost_at=lost))
    return cases, hist, accts, txns, bad


def build(con, seed: int = SEED) -> dict[str, pd.DataFrame]:
    rng = random.Random(seed)
    m_cases, m_hist, m_accts, bad = _marketplace(rng, _prep(con.execute(ORDERS_SQL).df()), set())
    f_cases, f_hist, f_accts, txns, bad2 = _fintech(rng)
    if bad + bad2:
        raise AssertionError(f"{len(bad + bad2)} cases did not produce their scenario's outcome, e.g. {(bad + bad2)[:5]}")
    cases = []
    for prefix, group in (("MKT", m_cases), ("FIN", f_cases)):
        rng.shuffle(group)  # ids must not reveal the scenario
        for i, c in enumerate(group, 1):
            c["dispute_id"] = f"{prefix}-{i:04d}"
        cases += group
    return dict(
        disputes=pd.DataFrame([{k: v for k, v in c.items() if k != "gt"} for c in cases]),
        ground_truth=pd.DataFrame([{"dispute_id": c["dispute_id"], **c["gt"]} for c in cases]),
        refund_history=pd.DataFrame(m_hist + f_hist), customer_accounts=pd.DataFrame(m_accts + f_accts),
        transactions=pd.DataFrame(txns),
        policies=pd.DataFrame([dict(policy_id=k, track=t, title=ti, body=b.strip(), is_synthetic=True)
                               for k, (t, ti, b) in P.POLICIES.items()]))


def main() -> None:
    con = connect()
    con.execute("CREATE SCHEMA IF NOT EXISTS synthetic")
    for name, df in build(con).items():
        con.register("df", df)
        con.execute(f"CREATE OR REPLACE TABLE synthetic.{name} AS SELECT * FROM df")
        con.unregister("df")
        print(f"synthetic.{name}: {len(df):,}")
    con.execute(Path(__file__).with_name("derived.sql").read_text())
    P.write_markdown()
    golden.export(con, SEED)
    print("\nDisputes by track x outcome:")
    print(con.execute("SELECT d.track, g.outcome, count(*) n FROM synthetic.disputes d JOIN synthetic.ground_truth g "
                      "USING (dispute_id) GROUP BY 1, 2 ORDER BY 1, 2").df().pivot(index="track", columns="outcome", values="n").fillna(0).astype(int))


if __name__ == "__main__":
    main()
