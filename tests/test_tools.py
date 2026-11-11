"""Tool service tests: point-in-time answers, errors, parity with the derived features, and the action log."""
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import needs_db

from disputedesk.tools import actions

pytestmark = needs_db


def get(client, tool, **params):
    return client.get(f"/tools/{tool}", params=params)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_get_order_hides_the_future(client, db):
    oid, delivered, purchased = db.execute(
        """SELECT order_id, order_delivered_customer_date, order_purchase_timestamp FROM real.olist_orders
           WHERE order_status = 'delivered' ORDER BY order_id LIMIT 1""").fetchone()
    after = get(client, "get_order", order_id=oid, as_of=(delivered + timedelta(days=30)).isoformat()).json()
    assert after["status"] == "delivered" and after["delivered_ts"] is not None
    before = get(client, "get_order", order_id=oid, as_of=(delivered - timedelta(hours=1)).isoformat()).json()
    assert before["status"] in ("shipped", "processing")
    assert before["delivered_ts"] is None and before["review_score"] is None
    assert get(client, "get_order", order_id=oid, as_of=(purchased - timedelta(days=1)).isoformat()).status_code == 404


def test_delivery_events_and_lateness(client, db):
    oid, delivered = db.execute(
        """SELECT order_id, order_delivered_customer_date FROM real.olist_orders
           WHERE order_status = 'delivered' ORDER BY order_id LIMIT 1""").fetchone()
    done = get(client, "get_delivery_events", order_id=oid, as_of=(delivered + timedelta(days=1)).isoformat()).json()
    names = [e["event"] for e in done["events"]]
    assert names[0] == "order_purchased" and names[-1] == "delivered" and done["minutes_late"] is not None
    early = get(client, "get_delivery_events", order_id=oid, as_of=(delivered - timedelta(hours=1)).isoformat()).json()
    assert "delivered" not in [e["event"] for e in early["events"]] and early["minutes_late"] is None


def test_errors(client):
    assert get(client, "get_order", order_id="nope", as_of="2018-01-01T00:00:00").status_code == 404
    assert get(client, "get_seller_stats", seller_id="nope", as_of="2018-01-01T00:00:00").status_code == 404
    assert get(client, "get_customer_history", customer_id="nope", as_of="2018-01-01T00:00:00").status_code == 404
    assert get(client, "get_payment", txn_id="nope", as_of="2018-01-01T00:00:00").status_code == 404
    assert get(client, "get_order", order_id="x").status_code == 422  # as_of is required
    assert get(client, "get_order", order_id="x", as_of="not-a-date").status_code == 422


def test_timezone_aware_as_of_is_normalised(client, db):
    oid, delivered = db.execute("SELECT order_id, order_delivered_customer_date FROM real.olist_orders "
                                "WHERE order_status = 'delivered' ORDER BY order_id LIMIT 1").fetchone()
    naive = (delivered + timedelta(days=3)).isoformat()
    assert get(client, "get_order", order_id=oid, as_of=naive).json() == \
        get(client, "get_order", order_id=oid, as_of=naive + "Z").json()


def test_seller_stats_count_only_earlier_deliveries(client, db):
    sid = db.execute("SELECT seller_id FROM derived.seller_history GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1").fetchone()[0]
    ts = db.execute("SELECT delivered_ts FROM derived.seller_history WHERE seller_id = ? ORDER BY delivered_ts OFFSET 40 LIMIT 1", [sid]).fetchone()[0]
    n, late = db.execute("SELECT count(*), sum(late::INT) FROM derived.seller_history WHERE seller_id = ? AND delivered_ts < ?", [sid, ts]).fetchone()
    s = get(client, "get_seller_stats", seller_id=sid, as_of=ts.isoformat()).json()
    assert (s["delivered_orders"], s["late_orders"]) == (n, late) and s["late_rate"] == pytest.approx(late / n)
    assert get(client, "get_seller_stats", seller_id=sid, as_of="2000-01-01T00:00:00").json()["delivered_orders"] == 0


def test_customer_history_matches_dispute_features(client, db):
    rows = db.execute("""SELECT d.customer_id, d.as_of, f.* EXCLUDE (dispute_id, customer_id, track)
        FROM synthetic.disputes d JOIN derived.dispute_features f USING (dispute_id)
        ORDER BY hash(d.dispute_id) LIMIT 150""").fetchall()
    cols = [c[0] for c in db.description]
    assert len(rows) == 150
    for row in rows:
        rec = dict(zip(cols, row, strict=True))
        h = get(client, "get_customer_history", customer_id=rec["customer_id"], as_of=rec["as_of"].isoformat()).json()
        for k in ("refunds_30d", "refunds_90d", "refunds_365d", "orders_total", "tenure_days", "prior_claims_on_time_orders"):
            assert h[k] == rec[k], (rec["customer_id"], k)
        assert h["refund_to_order_ratio"] == pytest.approx(rec["refund_to_order_ratio"])


def test_search_policy_filters_by_track(client):
    hits = get(client, "search_policy", query="late delivery refund", track="marketplace", k=10).json()
    assert hits and {h["track"] for h in hits} <= {"marketplace", "general"}
    assert get(client, "search_policy", query="x", track="crypto").status_code == 422
    assert get(client, "search_policy", query="x", k=0).status_code == 422


def test_refund_is_idempotent_and_rejects_changed_amount(client):
    body = {"case_id": uuid.uuid4().hex, "amount_usd": 12.5, "policy_ids": ["MKT-01"], "reason": "late"}
    first = client.post("/tools/issue_refund", json=body).json()
    second = client.post("/tools/issue_refund", json=body).json()
    assert first["duplicate"] is False and second["duplicate"] is True
    assert first["action_id"] == second["action_id"] and first["kind"] == "refund"
    assert client.post("/tools/issue_refund", json={**body, "amount_usd": 99}).status_code == 409


@pytest.mark.parametrize("patch", [{"amount_usd": 0}, {"amount_usd": -5}, {"policy_ids": []}, {"reason": ""}, {"case_id": ""}])
def test_refund_validation(client, patch):
    body = {"case_id": uuid.uuid4().hex, "amount_usd": 5, "policy_ids": ["MKT-01"], "reason": "late", **patch}
    assert client.post("/tools/issue_refund", json=body).status_code == 422


def test_escalation_is_recorded_once(client):
    body = {"case_id": uuid.uuid4().hex, "reason": "over the limit", "policy_ids": ["GEN-01"]}
    first = client.post("/tools/escalate_to_human", json=body).json()
    second = client.post("/tools/escalate_to_human", json=body).json()
    assert first["kind"] == "escalation" and first["amount_usd"] is None
    assert second["duplicate"] is True and second["action_id"] == first["action_id"]


def test_action_log_is_append_only(tmp_path):
    path = str(tmp_path / "a.db")
    actions.record(path, "case-1", "refund", 5.0, ["MKT-01"], "late")
    con = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE actions SET amount_usd = 500")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM actions")
    con.close()


def test_concurrent_refunds_for_one_case_pay_once(tmp_path):
    path = str(tmp_path / "a.db")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: actions.record(path, "case-9", "refund", 7.0, ["MKT-01"], "late"), range(8)))
    assert sum(not dup for _, dup in results) == 1 and len({row["action_id"] for row, _ in results}) == 1
    assert sqlite3.connect(path).execute("SELECT count(*) FROM actions").fetchone()[0] == 1
