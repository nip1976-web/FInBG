from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import os
from typing import Literal

import psycopg
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "dbname=finbg")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:5174",
    ).split(",")
    if origin.strip()
]

app = FastAPI(
    title="FinBG API",
    version="0.1.0",
    description="API системы финансового контроля предприятия.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


class StagingOperationUpdate(BaseModel):
    category: str | None = None
    likely_internal_transfer: bool | None = None
    possible_duplicate: bool | None = None
    validation_status: Literal[
        "pending", "ready", "excluded", "error", "imported"
    ] | None = None


@contextmanager
def database():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        yield connection


def money(value: Decimal | None) -> str:
    return str(value or Decimal("0.00"))


@app.get("/health")
def health() -> dict[str, str]:
    with database() as connection:
        row = connection.execute(
            "select current_database() as database, current_user as user"
        ).fetchone()
    return {
        "status": "ok",
        "database": row["database"],
        "database_user": row["user"],
    }


@app.get("/api/dashboard/summary")
def dashboard_summary(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> dict:
    with database() as connection:
        row = connection.execute(
            """
            with payment_totals as (
                select
                    coalesce(
                        sum(
                            case
                                when direction = 'inflow' then amount_rub
                                else -amount_rub
                            end
                        ) filter (where status = 'posted'),
                        0
                    ) as cash_balance,
                    coalesce(
                        sum(amount_rub) filter (
                            where direction = 'inflow'
                              and status = 'posted'
                              and (%(date_from)s::date is null or payment_date >= %(date_from)s)
                              and (%(date_to)s::date is null or payment_date <= %(date_to)s)
                        ),
                        0
                    ) as inflow,
                    coalesce(
                        sum(amount_rub) filter (
                            where direction = 'outflow'
                              and status = 'posted'
                              and (%(date_from)s::date is null or payment_date >= %(date_from)s)
                              and (%(date_to)s::date is null or payment_date <= %(date_to)s)
                        ),
                        0
                    ) as outflow
                from payments
            ),
            invoice_totals as (
                select
                    coalesce(sum(total_amount_rub) filter (
                        where invoice_type = 'customer'
                          and status in ('issued', 'partially_paid', 'paid')
                          and (%(date_from)s::date is null or invoice_date >= %(date_from)s)
                          and (%(date_to)s::date is null or invoice_date <= %(date_to)s)
                    ), 0) as revenue,
                    coalesce(sum(balance_rub) filter (
                        where invoice_type = 'customer'
                          and status in ('issued', 'partially_paid')
                    ), 0) as receivables,
                    coalesce(sum(balance_rub) filter (
                        where invoice_type = 'supplier'
                          and status in ('issued', 'partially_paid')
                    ), 0) as payables
                from v_invoice_balances
            )
            select *
            from payment_totals
            cross join invoice_totals
            """,
            {"date_from": date_from, "date_to": date_to},
        ).fetchone()

    return {
        "period": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "cash_balance_rub": money(row["cash_balance"]),
        "inflow_rub": money(row["inflow"]),
        "outflow_rub": money(row["outflow"]),
        "net_cash_flow_rub": money(row["inflow"] - row["outflow"]),
        "revenue_rub": money(row["revenue"]),
        "receivables_rub": money(row["receivables"]),
        "payables_rub": money(row["payables"]),
    }


@app.get("/api/imports/staging-summary")
def staging_import_summary() -> dict:
    with database() as connection:
        latest_batch = connection.execute(
            """
            select id, source_file, imported_at, imported_rows, status, period_from
            from import_batches
            order by id desc
            limit 1
            """
        ).fetchone()
        rows = connection.execute(
            """
            select
                source_sheet,
                direction,
                count(*) as operation_count,
                sum(amount_rub) as total_amount_rub,
                count(*) filter (where likely_internal_transfer) as internal_count,
                coalesce(
                    sum(amount_rub) filter (where likely_internal_transfer),
                    0
                ) as internal_amount_rub,
                count(*) filter (where possible_duplicate) as duplicate_count,
                count(*) filter (where validation_status = 'error') as error_count
            from staging_payment_operations
            group by source_sheet, direction
            order by source_sheet
            """
        ).fetchall()
        overview = connection.execute(
            """
            select
                count(*) as total_rows,
                count(*) filter (
                    where category is null or btrim(category) = ''
                ) as missing_category_count,
                count(*) filter (where likely_internal_transfer) as internal_count,
                count(*) filter (where possible_duplicate) as duplicate_count,
                count(*) filter (where validation_status = 'pending') as pending_count,
                count(*) filter (where validation_status = 'ready') as ready_count,
                count(*) filter (where validation_status = 'excluded') as excluded_count
            from staging_payment_operations
            """
        ).fetchone()

    return {
        "latest_batch": (
            {
                "id": latest_batch["id"],
                "source_file": latest_batch["source_file"],
                "imported_at": latest_batch["imported_at"].isoformat(),
                "imported_rows": latest_batch["imported_rows"],
                "status": latest_batch["status"],
                "period_from": latest_batch["period_from"].isoformat(),
            }
            if latest_batch
            else None
        ),
        "overview": dict(overview),
        "sheets": [
            {
                "source_sheet": row["source_sheet"],
                "direction": row["direction"],
                "operation_count": row["operation_count"],
                "total_amount_rub": money(row["total_amount_rub"]),
                "internal_count": row["internal_count"],
                "internal_amount_rub": money(row["internal_amount_rub"]),
                "duplicate_count": row["duplicate_count"],
                "error_count": row["error_count"],
            }
            for row in rows
        ],
    }


@app.get("/api/imports/categories")
def staging_categories() -> dict[str, list[str]]:
    with database() as connection:
        rows = connection.execute(
            """
            select category
            from (
                select distinct category
                from staging_payment_operations
                where category is not null and btrim(category) <> ''
                union
                select name as category
                from expense_categories
                where is_active
            ) categories
            order by category
            """
        ).fetchall()
    return {"items": [row["category"] for row in rows]}


@app.get("/api/imports/operations")
def staging_operations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    source_sheet: str | None = None,
    direction: Literal["inflow", "outflow"] | None = None,
    validation_status: Literal[
        "pending", "ready", "excluded", "error", "imported"
    ] | None = None,
    only_internal: bool = False,
    only_duplicates: bool = False,
    missing_category: bool = False,
    search: str | None = Query(default=None, max_length=200),
) -> dict:
    conditions: list[str] = []
    params: list[object] = []

    if source_sheet:
        conditions.append("source_sheet = %s")
        params.append(source_sheet)
    if direction:
        conditions.append("direction = %s")
        params.append(direction)
    if validation_status:
        conditions.append("validation_status = %s")
        params.append(validation_status)
    if only_internal:
        conditions.append("likely_internal_transfer")
    if only_duplicates:
        conditions.append("possible_duplicate")
    if missing_category:
        conditions.append("(category is null or btrim(category) = '')")
    if search and search.strip():
        term = f"%{search.strip()}%"
        conditions.append(
            """
            (
                counterparty ilike %s
                or explanation ilike %s
                or category ilike %s
                or source_sheet ilike %s
            )
            """
        )
        params.extend([term, term, term, term])

    where_sql = f"where {' and '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    with database() as connection:
        total = connection.execute(
            f"select count(*) as total from staging_payment_operations {where_sql}",
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            select
                id,
                source_sheet,
                source_row,
                direction,
                sequence_number,
                account_code,
                counterparty,
                amount_rub,
                operation_date,
                explanation,
                payment_basis,
                client,
                category,
                plan_code,
                manager,
                likely_internal_transfer,
                possible_duplicate,
                validation_status,
                validation_errors
            from staging_payment_operations
            {where_sql}
            order by operation_date desc, id desc
            limit %s offset %s
            """,
            [*params, page_size, offset],
        ).fetchall()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                **dict(row),
                "amount_rub": money(row["amount_rub"]),
                "operation_date": row["operation_date"].isoformat(),
            }
            for row in rows
        ],
    }


@app.patch("/api/imports/operations/{operation_id}")
def update_staging_operation(
    operation_id: int,
    update: StagingOperationUpdate,
) -> dict:
    fields = update.model_fields_set
    assignments: list[str] = []
    params: list[object] = []
    allowed = {
        "category": "category",
        "likely_internal_transfer": "likely_internal_transfer",
        "possible_duplicate": "possible_duplicate",
        "validation_status": "validation_status",
    }

    for field, column in allowed.items():
        if field not in fields:
            continue
        value = getattr(update, field)
        if field == "category" and isinstance(value, str):
            value = value.strip() or None
        assignments.append(f"{column} = %s")
        params.append(value)

    if not assignments:
        return {"updated": False, "id": operation_id}

    params.append(operation_id)
    with database() as connection:
        row = connection.execute(
            f"""
            update staging_payment_operations
            set {', '.join(assignments)},
                updated_at = now()
            where id = %s
            returning id, category, likely_internal_transfer,
                      possible_duplicate, validation_status
            """,
            params,
        ).fetchone()

    if not row:
        return {"updated": False, "id": operation_id}
    return {"updated": True, **dict(row)}
