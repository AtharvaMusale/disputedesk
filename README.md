# DisputeDesk

Agentic dispute and refund resolution for (a) marketplace/delivery and (b) fintech disputes.
A portfolio project demonstrating five production challenges of agentic AI:
orchestration, observability, cost control, evaluation, governance/safety.

> Status: **Phase 0 (scaffold)**. Results tables and architecture diagram arrive in Phase 8.

## Quickstart
```bash
cp .env.example .env      # fill in secrets
make setup                # venv + pinned deps
make load                 # build DuckDB from data/ (after the data steps below)
make test
```

## Layout
See `src/disputedesk/` — one package per concern (data, synthetic, tools, rag, risk, agents, llm, observability, governance).

## Data (honest notes — filled in Phase 1/2)
- **CFPB complaints (real):** narratives come from the CC0 HuggingFace mirror `BEE-spoke-data/consumer-finance-complaints` (the CFPB API and bulk CSV expose no narratives); it ends 2024-02-09. Card + money-transfer only, 10k sampled per group. Outcome labels are mapped from "Company response to consumer", which is **company self-reported**, so they are proxies, not ground truth.
- **Olist (real):** orders, payments, reviews, delivery timestamps.
- **Synthetic:** seeded generator; every row has `is_synthetic` and `scenario_type`. Ground truth comes from applying the written policy to the data, so it measures policy-following, not real-world correctness.
- Real and synthetic data live in separate DuckDB schemas (`real`, `synthetic`).

## Results
_TBD — measured numbers for each of the five challenges (Phase 8)._

_Docker/Postgres is deliberately deferred to a final containerization phase; storage is a local DuckDB file._
