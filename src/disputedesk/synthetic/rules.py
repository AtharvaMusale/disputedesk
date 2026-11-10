"""Ground truth: the written policies (policies.py) applied to facts. Pure functions, no I/O."""
from dataclasses import dataclass
from datetime import datetime

from disputedesk.synthetic import policies as P


@dataclass(frozen=True)
class Decision:
    outcome: str  # approve | partial | deny | escalate | request_info
    refund_usd: float
    policies: tuple[str, ...]
    rationale: str


@dataclass
class MktFacts:
    claim_type: str  # late | not_delivered | missing_item | damaged
    status: str
    days_late: float | None  # delivered orders only
    days_past_estimate: float  # as_of minus estimated delivery date
    total_usd: float
    freight_usd: float
    item_price_usd: float
    evidence: bool
    review_score: int | None
    seller_late_rate: float
    seller_orders: int
    account_age_days: int
    refunds_90d: int


@dataclass
class Txn:
    ts: datetime
    amount_usd: float
    merchant: str
    ip: str | None
    device: str | None
    disputed: bool = False


@dataclass
class FinFacts:
    claim_type: str  # unauthorized | unrecognized | duplicate_charge
    txn: Txn
    prior: list[Txn]
    card_reported_lost_at: datetime | None
    disputes_90d: int


def _deny(why, *pol):
    return Decision("deny", 0.0, pol, why)


def _paid(refund, total, why, *pol):
    return Decision("approve" if refund >= total - 0.005 else "partial", round(refund, 2), pol, why)


def decide_marketplace(f: MktFacts) -> Decision:
    if f.refunds_90d >= P.ABUSE_REFUNDS_90D:
        return Decision("escalate", 0.0, ("GEN-01",), "abuse pattern: fraud review before any refund")
    d = _mkt_base(f)
    if d.refund_usd > P.MKT_LIMIT:
        return Decision("escalate", 0.0, d.policies + ("GEN-01",),
                        f"valid, but ${d.refund_usd:.2f} exceeds the ${P.MKT_LIMIT:.0f} limit: human approval")
    return d


def _mkt_base(f: MktFacts) -> Decision:
    if f.claim_type == "late":
        late = f.days_late
        if f.status != "delivered" or late is None or late <= 0:
            return _deny("order was not late", "MKT-01")
        if (f.seller_late_rate >= P.SELLER_FAIL_RATE and f.seller_orders >= P.SELLER_FAIL_MIN_ORDERS
                and late >= 1):
            return _paid(f.total_usd, f.total_usd, "seller systemic failure: full refund", "MKT-04")
        if late >= P.FULL_REFUND_DAYS:
            return _paid(f.total_usd, f.total_usd, "10+ days late: full refund", "MKT-01")
        new = f.account_age_days < P.NEW_CUSTOMER_DAYS
        if late >= P.GRACE_DAYS or (new and late >= 1):
            pol = ("MKT-01",) if late >= P.GRACE_DAYS else ("MKT-05", "MKT-01")
            if f.freight_usd <= 0:
                return _deny("no shipping fee to refund", *pol)
            return _paid(f.freight_usd, f.total_usd, "late: shipping fee refund", *pol)
        return _deny("within grace period", "MKT-01")
    if f.claim_type == "not_delivered":
        if f.status == "canceled":
            return _paid(f.total_usd, f.total_usd, "cancelled but charged: full refund", "MKT-02")
        if f.status == "delivered":
            if f.review_score is not None and f.review_score >= P.POSITIVE_REVIEW:
                return Decision("escalate", 0.0, ("MKT-02",), "claims conflict with positive review: manual review")
            return _deny("carrier shows delivered", "MKT-02")
        if f.days_past_estimate >= P.NONDELIVERY_DAYS:
            return _paid(f.total_usd, f.total_usd, "not delivered 7+ days past estimate: full refund", "MKT-02")
        return _deny("not yet past the non-delivery window", "MKT-02")
    # missing_item | damaged
    if f.status != "delivered":
        return _deny("item claims apply to delivered orders only", "MKT-03")
    if not f.evidence:
        return Decision("request_info", 0.0, ("MKT-03",), "evidence required before deciding")
    return _paid(min(f.item_price_usd, f.total_usd), f.total_usd, "item refund (no shipping)", "MKT-03")


def compelling_evidence(f: FinFacts) -> bool:
    t, n = f.txn, 0
    lo, hi = P.VISA_WINDOW_DAYS
    for p in f.prior:
        age = (t.ts - p.ts).total_seconds() / 86400
        if p.disputed or not lo <= age <= hi:
            continue
        matches = ((t.ip is not None and p.ip == t.ip) + (t.device is not None and p.device == t.device)
                   + (p.merchant == t.merchant))
        n += matches >= P.VISA_MIN_MATCH  # >=2 of 3 elements always includes IP or device
    return n >= P.VISA_MIN_PRIOR


def decide_fintech(f: FinFacts) -> Decision:
    if f.disputes_90d >= P.ABUSE_REFUNDS_90D:
        return Decision("escalate", 0.0, ("GEN-01",), "abuse pattern: fraud review before any refund")
    d = _fin_base(f)
    if d.refund_usd > P.FIN_LIMIT:
        return Decision("escalate", 0.0, d.policies + ("GEN-01",),
                        f"valid, but ${d.refund_usd:.2f} exceeds the ${P.FIN_LIMIT:.0f} limit: human approval")
    return d


def _fin_base(f: FinFacts) -> Decision:
    t = f.txn
    if f.claim_type == "duplicate_charge":
        for p in f.prior:
            hours = (t.ts - p.ts).total_seconds() / 3600
            if p.merchant == t.merchant and p.amount_usd == t.amount_usd and 0 < hours <= P.DUP_WINDOW_HOURS:
                return _paid(t.amount_usd, t.amount_usd, "identical charge within 24h", "FIN-02")
        return _deny("no identical charge within 24h", "FIN-02")
    if compelling_evidence(f):
        if f.card_reported_lost_at is not None and f.card_reported_lost_at < t.ts:
            return Decision("escalate", 0.0, ("FIN-03",), "compelling evidence but card reported lost earlier: conflict")
        return _deny("compelling evidence rebuts the dispute", "FIN-03")
    known = sum(p.merchant == t.merchant and not p.disputed for p in f.prior)
    if f.claim_type == "unrecognized" and known >= P.KNOWN_MERCHANT_MIN:
        return _deny("customer has used this merchant before", "FIN-01")
    if t.ip is None and t.device is None:
        return Decision("request_info", 0.0, ("FIN-01",), "no IP or device data: more information needed")
    return _paid(t.amount_usd, t.amount_usd, "no evidence of authorization: refund", "FIN-01")
