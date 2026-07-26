from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import os

import psycopg
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
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
