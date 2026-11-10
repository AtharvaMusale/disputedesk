"""Data-quality checks on the loaded DuckDB. Each check is (name, SQL returning a violation count);
exit code 1 if any count is non-zero."""
import sys

from disputedesk.data.load import connect

CHECKS = {
    "orders: duplicate order_id": "SELECT count(*) - count(DISTINCT order_id) FROM real.olist_orders",
    "orders: delivered before purchase": """SELECT count(*) FROM real.olist_orders
        WHERE order_delivered_customer_date < order_purchase_timestamp""",
    "orders: delivered w/o delivery date": """SELECT count(*) FROM real.olist_orders
        WHERE order_status='delivered' AND order_delivered_customer_date IS NULL""",
    "items: orphan order_id": """SELECT count(*) FROM real.olist_order_items i
        LEFT JOIN real.olist_orders o USING (order_id) WHERE o.order_id IS NULL""",
    "items: orphan seller_id": """SELECT count(*) FROM real.olist_order_items i
        LEFT JOIN real.olist_sellers s USING (seller_id) WHERE s.seller_id IS NULL""",
    "payments: orphan order_id": """SELECT count(*) FROM real.olist_order_payments p
        LEFT JOIN real.olist_orders o USING (order_id) WHERE o.order_id IS NULL""",
    "payments: non-positive value": "SELECT count(*) FROM real.olist_order_payments WHERE payment_value <= 0",
    "reviews: score outside 1-5": "SELECT count(*) FROM real.olist_order_reviews WHERE review_score NOT BETWEEN 1 AND 5",
    "cfpb: duplicate complaint_id": "SELECT count(*) - count(DISTINCT complaint_id) FROM real.cfpb_complaints",
    "cfpb: narrative < 100 chars": "SELECT count(*) FROM real.cfpb_complaints WHERE length(narrative) < 100",
    "cfpb: label not approve/partial/deny": """SELECT count(*) FROM real.cfpb_complaints
        WHERE label NOT IN ('approve','partial','deny')""",
    "cfpb: marked synthetic": "SELECT count(*) FROM real.cfpb_complaints WHERE is_synthetic",
}

# Real Olist quirks: warn (don't fail) up to this many rows; a larger count means something broke.
KNOWN = {"orders: delivered w/o delivery date": 8, "payments: non-positive value": 9}


def main() -> int:
    con, bad = connect(read_only=True), 0
    for name, sql in CHECKS.items():
        n = con.execute(sql).fetchone()[0]
        status = "ok  " if n == 0 else "warn" if n <= KNOWN.get(name, 0) else "FAIL"
        bad += status == "FAIL"
        print(f"{status} {name}: {n:,}")
    print("\nCFPB label distribution:")
    print(con.execute("SELECT product_group, label, count(*) n FROM real.cfpb_complaints GROUP BY 1,2 ORDER BY 1,2").df().to_string(index=False))
    print("\nOlist order status:")
    print(con.execute("SELECT order_status, count(*) n FROM real.olist_orders GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))
    print("\nLate rate (delivered):", con.execute("SELECT round(avg(is_late::int),3) FROM derived.delay_severity").fetchone()[0])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
