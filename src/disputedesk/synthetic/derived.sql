-- What the tools return about a dispute's customer and order, as of the dispute date.
-- Only information from before as_of is used: no look-ahead.
CREATE OR REPLACE TABLE derived.dispute_features AS
WITH h AS (
  SELECT d.dispute_id,
         count(*) FILTER (WHERE x.ts > d.as_of - INTERVAL 30 DAY) AS refunds_30d,
         count(*) FILTER (WHERE x.ts > d.as_of - INTERVAL 90 DAY) AS refunds_90d,
         count(*) FILTER (WHERE x.ts > d.as_of - INTERVAL 365 DAY) AS refunds_365d,
         count(*) FILTER (WHERE x.on_time) AS prior_claims_on_time_orders
  FROM synthetic.disputes d
  JOIN synthetic.refund_history x ON x.customer_id = d.customer_id AND x.ts <= d.as_of
  GROUP BY 1
), real_orders AS (  -- marketplace: orders the customer had placed by the dispute date
  SELECT d.dispute_id, count(*) AS n
  FROM synthetic.disputes d
  JOIN real.olist_customers c ON c.customer_unique_id = d.customer_id
  JOIN real.olist_orders o ON o.customer_id = c.customer_id AND o.order_purchase_timestamp <= d.as_of
  GROUP BY 1
), txns AS (  -- fintech: transactions the customer had made by the dispute date
  SELECT d.dispute_id, count(*) AS n
  FROM synthetic.disputes d
  JOIN synthetic.transactions t ON t.customer_id = d.customer_id AND t.ts <= d.as_of
  GROUP BY 1
), me AS (
  SELECT d.dispute_id, d.as_of, s.seller_id, s.customer_state, s.minutes_late
  FROM synthetic.disputes d JOIN derived.delay_severity s ON s.order_id = d.order_id
), by_seller AS (  -- share of the seller's earlier deliveries that were no later than this one
  SELECT me.dispute_id, avg((p.minutes_late <= me.minutes_late)::INT) AS pct
  FROM me JOIN derived.delay_severity p ON p.seller_id = me.seller_id AND p.delivered_ts < me.as_of
  GROUP BY 1
), by_region AS (
  SELECT me.dispute_id, avg((p.minutes_late <= me.minutes_late)::INT) AS pct
  FROM me JOIN derived.delay_severity p ON p.customer_state = me.customer_state AND p.delivered_ts < me.as_of
  GROUP BY 1
), f AS (
  SELECT d.dispute_id, d.customer_id, d.track, d.as_of,
         coalesce(h.refunds_30d, 0) AS refunds_30d, coalesce(h.refunds_90d, 0) AS refunds_90d,
         coalesce(h.refunds_365d, 0) AS refunds_365d,
         coalesce(h.prior_claims_on_time_orders, 0) AS prior_claims_on_time_orders,
         -- every planted prior refund implies a prior order, so refunds never exceed orders
         coalesce(ro.n, tx.n, 0) + coalesce(h.refunds_365d, 0) AS orders_total
  FROM synthetic.disputes d
  LEFT JOIN h USING (dispute_id) LEFT JOIN real_orders ro USING (dispute_id) LEFT JOIN txns tx USING (dispute_id)
)
SELECT f.dispute_id, f.customer_id, f.track, f.refunds_30d, f.refunds_90d, f.refunds_365d, f.orders_total,
       f.refunds_365d / nullif(f.orders_total, 0)::DOUBLE AS refund_to_order_ratio,
       date_diff('day', a.signup_ts, f.as_of) AS tenure_days,
       f.prior_claims_on_time_orders,
       ds.is_late AS order_was_late,
       bs.pct AS delay_pctile_vs_seller, br.pct AS delay_pctile_vs_region
FROM f
LEFT JOIN synthetic.customer_accounts a ON a.customer_id = f.customer_id
LEFT JOIN synthetic.disputes d USING (dispute_id)
LEFT JOIN derived.delay_severity ds ON ds.order_id = d.order_id
LEFT JOIN by_seller bs USING (dispute_id) LEFT JOIN by_region br USING (dispute_id);
