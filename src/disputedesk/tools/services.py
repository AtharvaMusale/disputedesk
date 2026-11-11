"""Tool logic: read-only lookups against DuckDB, every one answered as of a given date (no look-ahead).

A tool hides anything that had not happened yet on `as_of`: a delivery after that date is not shown, a
review submitted after it is not shown, seller totals only count earlier deliveries.
"""
from datetime import datetime

from disputedesk.synthetic.policies import BRL_TO_USD as FX
from disputedesk.tools.models import (
    CustomerHistory,
    DeliveryEvent,
    DeliveryEvents,
    Order,
    Payment,
    PriorTransaction,
    SellerStats,
)


class NotFound(LookupError):
    pass


ORDER_SQL = f"""
WITH i AS (
  SELECT order_id, min(seller_id) AS seller_id, sum(freight_value) AS freight,
         arg_min(price, order_item_id) AS first_price
  FROM real.olist_order_items WHERE order_id = ? GROUP BY 1),
r AS (
  SELECT order_id, arg_max(review_score, review_answer_timestamp) AS review_score,
         max(review_answer_timestamp) AS review_ts
  FROM real.olist_order_reviews WHERE order_id = ? GROUP BY 1)
SELECT o.order_id, c.customer_unique_id, i.seller_id, o.order_status, o.order_purchase_timestamp,
       o.order_approved_at, o.order_delivered_carrier_date, o.order_delivered_customer_date,
       o.order_estimated_delivery_date,
       date_diff('minute', o.order_estimated_delivery_date, o.order_delivered_customer_date),
       p.total_paid * {FX}, coalesce(i.freight, 0) * {FX}, coalesce(i.first_price, 0) * {FX},
       r.review_score, r.review_ts
FROM real.olist_orders o
JOIN real.olist_customers c USING (customer_id)
JOIN derived.payment_risk p USING (order_id)
LEFT JOIN i USING (order_id) LEFT JOIN r USING (order_id)
WHERE o.order_id = ?"""


def _visible(ts: datetime | None, as_of: datetime) -> datetime | None:
    return ts if ts is not None and ts <= as_of else None


def _order(cur, order_id: str, as_of: datetime) -> dict:
    row = cur.execute(ORDER_SQL, [order_id] * 3).fetchone()
    if row is None or row[4] > as_of:  # unknown, or not yet placed on that date
        raise NotFound(f"order {order_id} not found as of {as_of}")
    (oid, cust, seller, status, purchased, approved, carrier, delivered, est, minutes_late,
     total, freight, price, score, review_ts) = row
    approved, carrier = _visible(approved, as_of), _visible(carrier, as_of)
    if delivered is not None and delivered > as_of:  # not delivered yet on that date
        delivered, minutes_late, status = None, None, ("shipped" if carrier else "processing")
    if review_ts is None or review_ts > as_of:
        score = None
    return dict(order_id=oid, customer_id=cust, seller_id=seller, status=status, purchase_ts=purchased,
                approved_ts=approved, carrier_ts=carrier, delivered_ts=delivered, estimated_delivery_ts=est,
                minutes_late=minutes_late, total_usd=total, freight_usd=freight, item_price_usd=price,
                review_score=score)


def get_order(cur, order_id: str, as_of: datetime) -> Order:
    return Order(**{k: v for k, v in _order(cur, order_id, as_of).items()
                    if k in Order.model_fields})


def get_delivery_events(cur, order_id: str, as_of: datetime) -> DeliveryEvents:
    o = _order(cur, order_id, as_of)
    named = [("order_purchased", o["purchase_ts"]), ("order_approved", o["approved_ts"]),
             ("handed_to_carrier", o["carrier_ts"]), ("delivered", o["delivered_ts"])]
    return DeliveryEvents(order_id=order_id, estimated_delivery_ts=o["estimated_delivery_ts"],
                          events=[DeliveryEvent(event=n, ts=t) for n, t in named if t is not None],
                          minutes_late=o["minutes_late"])


def get_customer_history(cur, customer_id: str, as_of: datetime) -> CustomerHistory:
    acct = cur.execute("SELECT track, signup_ts FROM synthetic.customer_accounts WHERE customer_id = ?",
                       [customer_id]).fetchone()
    if acct is None or acct[1] > as_of:
        raise NotFound(f"customer {customer_id} not found as of {as_of}")
    track, signup = acct
    r30, r90, r365, on_time = cur.execute(
        """SELECT count(*) FILTER (WHERE ts > ?::TIMESTAMP - INTERVAL 30 DAY),
                  count(*) FILTER (WHERE ts > ?::TIMESTAMP - INTERVAL 90 DAY),
                  count(*) FILTER (WHERE ts > ?::TIMESTAMP - INTERVAL 365 DAY),
                  count(*) FILTER (WHERE on_time)
           FROM synthetic.refund_history WHERE customer_id = ? AND ts <= ?""",
        [as_of, as_of, as_of, customer_id, as_of]).fetchone()
    txns = cur.execute(
        """SELECT txn_id, ts, amount_usd, merchant, ip, device_id, disputed FROM synthetic.transactions
           WHERE customer_id = ? AND ts <= ? ORDER BY ts""", [customer_id, as_of]).fetchall()
    if track == "marketplace":  # orders placed by the dispute date, plus one implied by each prior refund
        placed = cur.execute(
            """SELECT count(*) FROM real.olist_orders o JOIN real.olist_customers c USING (customer_id)
               WHERE c.customer_unique_id = ? AND o.order_purchase_timestamp <= ?""",
            [customer_id, as_of]).fetchone()[0]
    else:
        placed = len(txns)
    orders_total = placed + r365
    return CustomerHistory(
        customer_id=customer_id, track=track, as_of=as_of, signup_ts=signup,
        tenure_days=(as_of - signup).days, orders_total=orders_total, refunds_30d=r30, refunds_90d=r90,
        refunds_365d=r365, refund_to_order_ratio=r365 / orders_total if orders_total else 0.0,
        prior_claims_on_time_orders=on_time,
        prior_transactions=[PriorTransaction(txn_id=t[0], ts=t[1], amount_usd=t[2], merchant=t[3], ip=t[4],
                                             device_id=t[5], disputed=t[6]) for t in txns] if track == "fintech" else [])


def get_seller_stats(cur, seller_id: str, as_of: datetime) -> SellerStats:
    if cur.execute("SELECT 1 FROM real.olist_sellers WHERE seller_id = ?", [seller_id]).fetchone() is None:
        raise NotFound(f"seller {seller_id} not found")
    delivered, late = cur.execute(
        """SELECT delivered_cum, late_cum FROM derived.seller_history
           WHERE seller_id = ? AND delivered_ts < ? ORDER BY delivered_ts DESC LIMIT 1""",
        [seller_id, as_of]).fetchone() or (0, 0)
    return SellerStats(seller_id=seller_id, as_of=as_of, delivered_orders=delivered, late_orders=late,
                       late_rate=late / delivered if delivered else 0.0)


def get_payment(cur, txn_id: str, as_of: datetime) -> Payment:
    row = cur.execute(
        """SELECT t.txn_id, t.customer_id, t.ts, t.amount_usd, t.merchant, t.descriptor, t.ip, t.device_id,
                  a.card_reported_lost_at
           FROM synthetic.transactions t JOIN synthetic.customer_accounts a USING (customer_id)
           WHERE t.txn_id = ?""", [txn_id]).fetchone()
    if row is None or row[2] > as_of:
        raise NotFound(f"transaction {txn_id} not found as of {as_of}")
    return Payment(txn_id=row[0], customer_id=row[1], ts=row[2], amount_usd=row[3], merchant=row[4],
                   descriptor=row[5], ip=row[6], device_id=row[7],
                   card_reported_lost_at=_visible(row[8], as_of))
