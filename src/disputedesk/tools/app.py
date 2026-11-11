"""FastAPI service exposing the agent tools. Run: make api

Reads go to DuckDB through a read-only connection (one cursor per request); the two actions write to the
append-only SQLite log. Every read tool takes `as_of`, the dispute date, and answers only from what was
known then.
"""
from datetime import UTC, datetime
from typing import Annotated, Literal

import duckdb
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from disputedesk.config import settings
from disputedesk.data.load import connect
from disputedesk.rag import search
from disputedesk.tools import actions, services
from disputedesk.tools.models import (
    ActionResult,
    CustomerHistory,
    DeliveryEvents,
    EscalationRequest,
    Order,
    Payment,
    PolicyHit,
    RefundRequest,
    SellerStats,
)


def create_app(db_path: str | None = None, actions_path: str | None = None,
               chroma_path: str | None = None) -> FastAPI:
    con = connect(read_only=True, path=db_path)
    actions_path = actions_path or settings.actions_db_path
    app = FastAPI(title="DisputeDesk tools", version="0.1.0")

    def cursor():
        cur = con.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def as_of_param(as_of: Annotated[datetime, Query(description="Dispute date; the tool answers as of then")]):
        return as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of

    Cur = Annotated[duckdb.DuckDBPyConnection, Depends(cursor)]
    AsOf = Annotated[datetime, Depends(as_of_param)]

    @app.exception_handler(services.NotFound)
    def not_found(_: Request, e: services.NotFound):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(actions.Conflict)
    def conflict(_: Request, e: actions.Conflict):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(search.IndexMissing)
    def index_missing(_: Request, e: search.IndexMissing):
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/tools/get_order")
    def get_order(order_id: str, as_of: AsOf, cur: Cur) -> Order:
        return services.get_order(cur, order_id, as_of)

    @app.get("/tools/get_delivery_events")
    def get_delivery_events(order_id: str, as_of: AsOf, cur: Cur) -> DeliveryEvents:
        return services.get_delivery_events(cur, order_id, as_of)

    @app.get("/tools/get_customer_history")
    def get_customer_history(customer_id: str, as_of: AsOf, cur: Cur) -> CustomerHistory:
        return services.get_customer_history(cur, customer_id, as_of)

    @app.get("/tools/get_seller_stats")
    def get_seller_stats(seller_id: str, as_of: AsOf, cur: Cur) -> SellerStats:
        return services.get_seller_stats(cur, seller_id, as_of)

    @app.get("/tools/get_payment")
    def get_payment(txn_id: str, as_of: AsOf, cur: Cur) -> Payment:
        return services.get_payment(cur, txn_id, as_of)

    @app.get("/tools/search_policy")
    def search_policy(query: str, track: Literal["marketplace", "fintech"] | None = None,
                      k: Annotated[int, Query(ge=1, le=10)] = 3) -> list[PolicyHit]:
        return list(search.search_policy(query, track, k, chroma_path))

    @app.post("/tools/issue_refund")
    def issue_refund(req: RefundRequest) -> ActionResult:
        row, duplicate = actions.record(actions_path, req.case_id, "refund", req.amount_usd, req.policy_ids, req.reason)
        return ActionResult(**row, duplicate=duplicate)

    @app.post("/tools/escalate_to_human")
    def escalate_to_human(req: EscalationRequest) -> ActionResult:
        row, duplicate = actions.record(actions_path, req.case_id, "escalation", None, req.policy_ids, req.reason)
        return ActionResult(**row, duplicate=duplicate)

    return app
