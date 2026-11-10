from datetime import datetime, timedelta

from disputedesk.synthetic import rules as R


def mkt(**kw):
    base = dict(claim_type="late", status="delivered", days_late=12.0, days_past_estimate=12.0, total_usd=40.0,
                freight_usd=5.0, item_price_usd=30.0, evidence=False, review_score=None, seller_late_rate=0.05,
                seller_orders=100, account_age_days=400, refunds_90d=0)
    return R.decide_marketplace(R.MktFacts(**{**base, **kw}))


def test_late_tiers():
    assert (mkt().outcome, mkt().refund_usd) == ("approve", 40.0)
    assert (mkt(days_late=5).outcome, mkt(days_late=5).refund_usd) == ("partial", 5.0)
    assert mkt(days_late=2).outcome == "deny"
    assert mkt(days_late=-1).outcome == "deny"
    assert mkt(days_late=5, freight_usd=0).outcome == "deny"


def test_overrides_and_precedence():
    assert mkt(days_late=2, account_age_days=10).policies == ("MKT-05", "MKT-01")  # new customer
    d = mkt(days_late=2, seller_late_rate=0.25)
    assert (d.outcome, d.policies) == ("approve", ("MKT-04",))  # seller failure beats grace period
    assert mkt(days_late=2, seller_late_rate=0.25, seller_orders=5).outcome == "deny"  # too few orders
    assert mkt(total_usd=150).outcome == "escalate"  # over the $100 limit
    assert mkt(refunds_90d=3).outcome == "escalate"  # abuse beats a valid claim


def test_non_delivery_and_items():
    assert mkt(claim_type="not_delivered", status="canceled").outcome == "approve"
    assert mkt(claim_type="not_delivered", status="shipped", days_past_estimate=9).outcome == "approve"
    assert mkt(claim_type="not_delivered", status="shipped", days_past_estimate=3).outcome == "deny"
    assert mkt(claim_type="not_delivered", review_score=5).outcome == "escalate"  # conflicting evidence
    assert mkt(claim_type="not_delivered", review_score=2).outcome == "deny"
    assert mkt(claim_type="missing_item").outcome == "request_info"
    d = mkt(claim_type="missing_item", evidence=True)
    assert (d.outcome, d.refund_usd) == ("partial", 30.0)


T0 = datetime(2023, 9, 1)


def tx(days_ago=0, merchant="Uber", ip="1.1.1.1", dev="d1", amount=50.0, disputed=False):
    return R.Txn(T0 - timedelta(days=days_ago), amount, merchant, ip, dev, disputed)


def fin(claim="unauthorized", txn=None, prior=(), lost=None, d90=0):
    return R.decide_fintech(R.FinFacts(claim, txn or tx(), list(prior), lost, d90))


def test_compelling_evidence():
    two = [tx(200, merchant="X"), tx(300, merchant="Y")]  # match on ip + device
    assert fin(prior=two).outcome == "deny"
    assert fin(prior=two[:1]).outcome == "approve"  # only one qualifying txn
    assert fin(prior=[tx(200, dev="o1", merchant="X"), tx(300, dev="o2", merchant="Y")]).outcome == "approve"  # 1 element
    assert fin(prior=[tx(200, disputed=True, merchant="X"), tx(300, merchant="Y")]).outcome == "approve"
    assert fin(prior=[tx(120, merchant="X"), tx(365, merchant="Y")]).outcome == "deny"  # window edges inclusive
    assert fin(prior=[tx(119, merchant="X"), tx(300, merchant="Y")]).outcome == "approve"
    assert fin(prior=two, lost=T0 - timedelta(days=3)).outcome == "escalate"  # lost before: conflict
    assert fin(prior=two, lost=T0 + timedelta(days=1)).outcome == "deny"  # reported lost after: no conflict


def test_fintech_other_rules():
    t = tx(0, amount=20.0)
    assert fin("duplicate_charge", t, [tx(0.5, amount=20.0)]).outcome == "approve"
    assert fin("duplicate_charge", t, [tx(30, amount=20.0)]).outcome == "deny"
    assert fin("duplicate_charge", t, [tx(0.5, amount=10.0)]).outcome == "deny"
    assert fin(txn=tx(ip=None, dev=None)).outcome == "request_info"
    known = [tx(10, ip="9.9.9.9", dev="z1"), tx(20, ip="8.8.8.8", dev="z2")]  # same merchant, different ip/device
    assert fin("unrecognized", prior=known).outcome == "deny"
    assert fin("unauthorized", prior=known).outcome == "approve"  # known-merchant rule is for unrecognized only
    assert fin(txn=tx(amount=600)).outcome == "escalate"
    assert fin(d90=3).outcome == "escalate"
