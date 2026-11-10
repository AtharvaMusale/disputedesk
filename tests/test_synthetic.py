"""Checks on the generated synthetic layer. Skipped until `make load && make synth` has run."""
import json
from itertools import pairwise
from pathlib import Path

import duckdb
import pytest

from disputedesk.config import settings
from disputedesk.synthetic import generate
from disputedesk.synthetic import policies as P
from disputedesk.synthetic.golden import alloc

pytestmark = pytest.mark.skipif(not Path(settings.duckdb_path).exists(), reason="run make load && make synth")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(settings.duckdb_path, read_only=True)
    try:
        c.execute("SELECT 1 FROM synthetic.disputes LIMIT 1")
    except duckdb.Error:
        pytest.skip("run make synth")
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_disputes_are_consistent(con):
    assert one(con, "SELECT count(*) FROM synthetic.disputes WHERE NOT is_synthetic") == 0
    assert one(con, "SELECT count(*) - count(DISTINCT dispute_id) FROM synthetic.disputes") == 0
    assert one(con, "SELECT count(*) FROM synthetic.disputes d LEFT JOIN synthetic.ground_truth g USING (dispute_id) WHERE g.dispute_id IS NULL") == 0
    assert one(con, "SELECT count(*) FROM synthetic.disputes WHERE (order_id IS NULL) = (txn_id IS NULL)") == 0
    assert one(con, "SELECT count(DISTINCT outcome) FROM synthetic.ground_truth") == 5
    assert one(con, "SELECT count(*) FROM synthetic.disputes WHERE NOT injection_attempt AND (complaint_text ILIKE '%SYSTEM:%' OR complaint_text ILIKE '%[admin override]%')") == 0
    assert one(con, "SELECT count(*) FROM synthetic.disputes WHERE injection_attempt") > 0


def test_marketplace_orders_are_real(con):
    assert one(con, "SELECT count(*) FROM synthetic.disputes d LEFT JOIN real.olist_orders o USING (order_id) WHERE d.order_id IS NOT NULL AND o.order_id IS NULL") == 0


def test_abuse_history_only_where_planted(con):
    rows = con.execute("SELECT d.scenario_type, f.refunds_90d FROM synthetic.disputes d JOIN derived.dispute_features f USING (dispute_id)").fetchall()
    for scenario, n90 in rows:
        assert (n90 >= P.ABUSE_REFUNDS_90D) == (scenario in ("serial_abuser", "ft_serial")), scenario


def test_policy_docs_exist(con):
    assert one(con, "SELECT count(*) FROM synthetic.policies") == 10
    assert all(Path("policies", f"{pid}.md").exists() for pid in P.POLICIES)


def test_golden_set(con):
    rows = [json.loads(line) for line in Path("data/golden/golden_synthetic.jsonl").read_text().splitlines()]
    assert len(rows) == 180 and len({r["case_id"] for r in rows}) == 180
    diffs = [r["difficulty"] for r in rows]
    assert (diffs.count("easy"), diffs.count("medium"), diffs.count("hard")) == (54, 72, 54)
    assert all(r["is_synthetic"] for r in rows)
    real = [json.loads(line) for line in Path("data/golden/golden_cfpb.jsonl").read_text().splitlines()]
    assert len(real) == 42 and not any(r["is_synthetic"] for r in real)
    assert {r["expected_outcome"] for r in real} == {"approve", "partial", "deny"}


def test_alloc_balances_and_redistributes():
    assert alloc(30, {"a": 100, "b": 100, "c": 100}) == {"a": 10, "b": 10, "c": 10}
    assert alloc(30, {"a": 4, "b": 100, "c": 100}) == {"a": 4, "b": 13, "c": 13}
    assert sum(alloc(50, {"a": 3, "b": 4}).values()) == 7  # not enough available: take what exists


def test_generation_is_deterministic(con):
    a, b = generate.build(con), generate.build(con)
    assert a["disputes"].complaint_text.tolist() == b["disputes"].complaint_text.tolist()
    assert a["ground_truth"].outcome.tolist() == b["ground_truth"].outcome.tolist()


def test_seller_failure_uses_only_past_deliveries(con):
    """Seller stats must be as of the dispute date: strictly earlier deliveries only (no look-ahead)."""
    rows = con.execute("""SELECT d.scenario_type,
        coalesce(h.late_cum / h.delivered_cum, 0) >= ? AND coalesce(h.delivered_cum, 0) >= ? AS failing
        FROM synthetic.disputes d
        JOIN (SELECT order_id, min(seller_id) AS sid FROM real.olist_order_items GROUP BY 1) i USING (order_id)
        ASOF LEFT JOIN derived.seller_history h ON h.seller_id = i.sid AND d.as_of > h.delivered_ts
        WHERE d.track = 'marketplace'""", [P.SELLER_FAIL_RATE, P.SELLER_FAIL_MIN_ORDERS]).fetchall()
    for scenario, failing in rows:
        if scenario == "systemic_merchant":
            assert failing
        elif scenario in ("clear_approve_late", "partial_late", "within_grace_deny", "policy_conflict", "over_limit_valid"):
            assert not failing, scenario


def test_customer_history_is_possible_and_point_in_time(con):
    assert one(con, "SELECT count(*) FROM derived.dispute_features WHERE refund_to_order_ratio > 1 OR orders_total < refunds_365d") == 0
    # marketplace order counts include only real orders placed on or before the dispute date
    assert one(con, """SELECT count(*) FROM synthetic.disputes d JOIN derived.dispute_features f USING (dispute_id)
        WHERE d.track = 'marketplace' AND f.orders_total - f.refunds_365d <> (
          SELECT count(*) FROM real.olist_orders o JOIN real.olist_customers c USING (customer_id)
          WHERE c.customer_unique_id = d.customer_id AND o.order_purchase_timestamp <= d.as_of)""") == 0


def test_delay_percentiles_defined_for_delivered_orders(con):
    assert one(con, """SELECT count(*) FROM derived.dispute_features f JOIN synthetic.disputes d USING (dispute_id)
        JOIN real.olist_orders o USING (order_id) WHERE o.order_status = 'delivered' AND (
          f.delay_pctile_vs_seller IS NULL OR f.delay_pctile_vs_region IS NULL
          OR f.delay_pctile_vs_seller NOT BETWEEN 0 AND 1)""") == 0


def test_fintech_ids_do_not_reveal_scenario(con):
    seq = [s for (s,) in con.execute("SELECT scenario_type FROM synthetic.disputes WHERE track = 'fintech' ORDER BY customer_id").fetchall()]
    assert sum(a != b for a, b in pairwise(seq)) > 100  # sequential ids would give only 10 changes
    assert one(con, "SELECT count(*) FROM synthetic.disputes d LEFT JOIN synthetic.transactions t USING (txn_id) WHERE d.track = 'fintech' AND t.txn_id IS NULL") == 0
    assert one(con, "SELECT count(*) FROM synthetic.disputes d LEFT JOIN synthetic.customer_accounts a USING (customer_id) WHERE a.customer_id IS NULL") == 0


def test_connect_read_only_rejects_writes():
    from disputedesk.data.load import connect

    c = connect(read_only=True)
    with pytest.raises(duckdb.Error):
        c.execute("CREATE TABLE should_not_exist (a INT)")
    c.close()
