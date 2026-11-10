-- Derived insight tables from REAL data only (DuckDB SQL). Refund history and dispute patterns
-- need the synthetic disputes and are built in Phase 2.
CREATE OR REPLACE TABLE derived.delay_severity AS
WITH o AS (
  SELECT o.order_id, o.customer_id, c.customer_state,
         (SELECT min(seller_id) FROM real.olist_order_items i WHERE i.order_id = o.order_id) AS seller_id,
         date_diff('minute', o.order_estimated_delivery_date, o.order_delivered_customer_date) AS minutes_late
  FROM real.olist_orders o JOIN real.olist_customers c USING (customer_id)
  WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
)
SELECT *, minutes_late > 0 AS is_late,
       percent_rank() OVER (PARTITION BY seller_id ORDER BY minutes_late) AS pctile_vs_seller,
       percent_rank() OVER (PARTITION BY customer_state ORDER BY minutes_late) AS pctile_vs_region
FROM o;

CREATE OR REPLACE TABLE derived.seller_reliability AS
WITH s AS (
  SELECT DISTINCT i.seller_id, o.order_id, o.order_status,
         (o.order_delivered_customer_date > o.order_estimated_delivery_date) AS late
  FROM real.olist_order_items i JOIN real.olist_orders o USING (order_id)
), r AS (
  SELECT order_id, avg(review_score) AS review_score FROM real.olist_order_reviews GROUP BY 1
)
SELECT s.seller_id, count(*) AS n_orders,
       avg((order_status = 'canceled')::int) AS cancel_rate,
       avg(late::int) FILTER (WHERE order_status = 'delivered') AS late_rate,
       avg(r.review_score) AS avg_review
FROM s LEFT JOIN r USING (order_id)
GROUP BY s.seller_id;

CREATE OR REPLACE TABLE derived.customer_profile AS
SELECT c.customer_unique_id, count(DISTINCT o.order_id) AS n_orders,
       min(o.order_purchase_timestamp) AS first_order, max(o.order_purchase_timestamp) AS last_order,
       date_diff('day', min(o.order_purchase_timestamp), max(o.order_purchase_timestamp)) AS tenure_days,
       count(DISTINCT o.order_id) FILTER (WHERE o.order_status = 'canceled') AS n_canceled,
       count(DISTINCT o.order_id) FILTER (WHERE r.review_score <= 2) AS n_low_reviews
FROM real.olist_customers c JOIN real.olist_orders o USING (customer_id)
LEFT JOIN real.olist_order_reviews r USING (order_id)
GROUP BY c.customer_unique_id;

CREATE OR REPLACE TABLE derived.payment_risk AS
SELECT order_id, sum(payment_value) AS total_paid, max(payment_installments) AS max_installments,
       count(DISTINCT payment_type) AS n_payment_types,
       coalesce(sum(payment_value) FILTER (WHERE payment_type = 'voucher'), 0) / nullif(sum(payment_value), 0) AS voucher_share,
       bool_or(payment_type = 'credit_card') AS paid_by_card
FROM real.olist_order_payments GROUP BY order_id;

-- Keyword severity tags for CFPB narratives (rule-based; not a sentiment model).
CREATE OR REPLACE TABLE derived.cfpb_tags AS
SELECT complaint_id,
       regexp_matches(narrative, '\$\s?[0-9]') AS mentions_amount,
       regexp_matches(narrative, '(attorney|lawyer|lawsuit|sue |legal action|small claims)', 'i') AS legal_threat,
       regexp_matches(narrative, '(fraud|scam|stolen|unauthorized|did not authorize|identity theft)', 'i') AS fraud_language,
       regexp_matches(narrative, '(urgent|immediately|homeless|can.t pay|cannot pay|evict)', 'i') AS hardship_language,
       length(narrative) AS narrative_len
FROM real.cfpb_complaints;
