"""Load raw files into DuckDB (schema `real`) and build derived tables. Re-runnable."""
from pathlib import Path

import duckdb

from disputedesk.config import settings

OLIST = Path("data/raw/olist")
OLIST_TABLES = ["customers", "sellers", "products", "orders", "order_items", "order_payments", "order_reviews"]


def connect(read_only: bool = False, path: str | None = None) -> duckdb.DuckDBPyConnection:
    # DuckDB allows one read-write process; services and checks that only read should pass read_only=True
    return duckdb.connect(path or settings.duckdb_path, read_only=read_only)


def main() -> None:
    con = connect()
    for s in ("real", "synthetic", "derived", "audit"):  # real/synthetic kept apart on purpose
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    for t in OLIST_TABLES:
        con.execute(f"CREATE OR REPLACE TABLE real.olist_{t} AS "
                    f"SELECT * FROM read_csv('{OLIST}/olist_{t}_dataset.csv', header=true)")
    con.execute("""CREATE OR REPLACE TABLE real.cfpb_complaints AS SELECT
        "Complaint ID" AS complaint_id, "Date received" AS date_received, Product AS product,
        "Sub-product" AS sub_product, Issue AS issue, "Sub-issue" AS sub_issue,
        "Consumer complaint narrative" AS narrative, Company AS company, State AS state,
        "Company response to consumer" AS company_response, label, "group" AS product_group, is_synthetic
        FROM read_parquet('data/processed/cfpb_sample.parquet')""")
    con.execute(Path(__file__).with_name("derived.sql").read_text())
    for schema, name in con.execute("SELECT table_schema, table_name FROM information_schema.tables "
                                    "WHERE table_schema IN ('real','derived') ORDER BY 1,2").fetchall():
        print(f"{schema}.{name}: {con.execute(f'SELECT count(*) FROM {schema}.{name}').fetchone()[0]:,}")


if __name__ == "__main__":
    main()
