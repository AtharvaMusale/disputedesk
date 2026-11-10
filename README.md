# DisputeDesk

Agentic dispute and refund resolution for (a) marketplace/delivery and (b) fintech disputes.
A portfolio project demonstrating five production challenges of agentic AI:
orchestration, observability, cost control, evaluation, governance/safety.

> Status: **Phase 1 done** (data pipeline). Next: synthetic scenarios and golden eval set.
> Architecture diagram and results tables arrive in Phase 8.

## Quickstart (data pipeline)
Needs Python 3.14, ~3 GB free disk, and a free [Kaggle API token](https://www.kaggle.com/settings) saved to `~/.kaggle/access_token`.

```bash
cp .env.example .env      # defaults work for the data pipeline
make setup                # venv + pinned deps
make olist-download       # Olist e-commerce data (~45 MB)
make cfpb-download        # CFPB complaints with narratives (~925 MB), filtered
make cfpb-sample          # 10k complaints per group
make load                 # build data/disputedesk.duckdb (real + derived tables)
make dq                   # data-quality checks
make test
```

## Layout
See `src/disputedesk/` — one package per concern (data, synthetic, tools, rag, risk, agents, llm, observability, governance).

## Data (honest notes)
- **CFPB complaints (real):** narratives come from the CC0 HuggingFace mirror `BEE-spoke-data/consumer-finance-complaints` (the CFPB API and bulk CSV expose no narratives); it ends 2024-02-09. Card + money-transfer only, 10k sampled per group. Outcome labels are mapped from "Company response to consumer", which is **company self-reported**, so they are proxies, not ground truth.
- **Olist (real):** ~99k Brazilian marketplace orders (2016-2018) with payments, reviews and delivery timestamps. No disputes or refunds exist in it, so those are synthetic. License: CC BY-NC-SA 4.0 (non-commercial); the data is downloaded, not redistributed in this repo.
- **Synthetic (Phase 2, not built yet):** seeded generator; every row has `is_synthetic` and `scenario_type`. Ground truth comes from applying the written policy to the data, so it measures policy-following, not real-world correctness.
- Real and synthetic data live in separate DuckDB schemas (`real`, `synthetic`); insight tables are in `derived`.

## Results
_TBD — measured numbers for each of the five challenges (Phase 8)._

_Docker/Postgres is deliberately deferred to a final containerization phase; storage is a local DuckDB file._
