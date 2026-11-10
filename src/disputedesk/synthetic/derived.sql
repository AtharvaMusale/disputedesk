-- Features about each dispute's customer, as of the dispute date (what get_customer_history will return).
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
), t AS (
  SELECT customer_id, count(*) AS n_txns FROM synthetic.transactions GROUP BY 1
)
SELECT d.dispute_id, d.customer_id, d.track,
       coalesce(h.refunds_30d, 0) AS refunds_30d,
       coalesce(h.refunds_90d, 0) AS refunds_90d,
       coalesce(h.refunds_365d, 0) AS refunds_365d,
       coalesce(p.n_orders, t.n_txns, 1) AS orders_total,
       coalesce(h.refunds_365d, 0) / coalesce(p.n_orders, t.n_txns, 1)::DOUBLE AS refund_to_order_ratio,
       date_diff('day', a.signup_ts, d.as_of) AS tenure_days,
       coalesce(h.prior_claims_on_time_orders, 0) AS prior_claims_on_time_orders,
       ds.is_late AS order_was_late
FROM synthetic.disputes d
LEFT JOIN h USING (dispute_id)
LEFT JOIN t ON t.customer_id = d.customer_id
LEFT JOIN derived.customer_profile p ON p.customer_unique_id = d.customer_id
LEFT JOIN synthetic.customer_accounts a ON a.customer_id = d.customer_id
LEFT JOIN derived.delay_severity ds ON ds.order_id = d.order_id;
