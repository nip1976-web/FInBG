from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from typing import Literal
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import psycopg
from fastapi import FastAPI, HTTPException, Query
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

EUR_RATE_CACHE: tuple[datetime, dict] | None = None


class StagingOperationUpdate(BaseModel):
    category: str | None = None
    likely_internal_transfer: bool | None = None
    possible_duplicate: bool | None = None
    validation_status: Literal[
        "pending", "ready", "excluded", "error", "imported"
    ] | None = None


class ManualPaymentAllocationCreate(BaseModel):
    payment_id: int
    allocated_amount_rub: Decimal


@contextmanager
def database():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        yield connection


def money(value: Decimal | None) -> str:
    return str((value or Decimal("0.00")).quantize(Decimal("0.01")))


def ilike_term(value: str) -> str:
    """Escape LIKE metacharacters so user input can't widen its own filter."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/api/reference/eur-rate")
def current_eur_rate() -> dict:
    global EUR_RATE_CACHE
    now = datetime.now(timezone.utc)
    if EUR_RATE_CACHE and now - EUR_RATE_CACHE[0] < timedelta(hours=6):
        return EUR_RATE_CACHE[1]
    try:
        request = Request(
            "https://www.cbr.ru/scripts/XML_daily.asp",
            headers={"User-Agent": "FinBG/1.0"},
        )
        with urlopen(request, timeout=12) as response:
            root = ElementTree.fromstring(response.read())
        eur = next(item for item in root.findall("Valute") if item.findtext("CharCode") == "EUR")
        nominal = Decimal(eur.findtext("Nominal", "1").replace(",", "."))
        value = Decimal(eur.findtext("Value", "0").replace(",", "."))
        payload = {
            "rate": money(value / nominal),
            "rate_date": root.attrib.get("Date"),
            "source": "ЦБ РФ",
        }
        EUR_RATE_CACHE = (now, payload)
        return payload
    except Exception as error:
        raise HTTPException(status_code=503, detail="Не удалось получить курс EUR с сайта ЦБ РФ.") from error


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


@app.get("/api/data/summary")
def loaded_data_summary() -> dict:
    with database() as connection:
        payments = connection.execute(
            """
            select
                count(*) as row_count,
                coalesce(sum(amount_rub), 0) as total_amount_rub,
                count(*) filter (where is_internal_transfer) as internal_count,
                coalesce(
                    sum(amount_rub) filter (where is_internal_transfer),
                    0
                ) as internal_amount_rub,
                min(payment_date) as date_from,
                max(payment_date) as date_to
            from payments
            where source = 'payment_battery'
              and direction = 'inflow'
            """
        ).fetchone()
        receipts = connection.execute(
            """
            select
                count(*) as row_count,
                coalesce(sum(paid_amount_rub), 0) as total_amount_rub,
                count(distinct customer_name) as customer_count,
                min(payment_date) as date_from,
                max(payment_date) as date_to
            from customer_receipts
            """
        ).fetchone()
        deals = connection.execute(
            """
            select
                count(*) as row_count,
                coalesce(sum(planned_revenue_rub), 0) as total_amount_rub,
                coalesce(sum(paid_amount_rub), 0) as paid_amount_rub,
                coalesce(
                    sum(balance_rub) filter (where balance_rub > 0),
                    0
                ) as open_balance_rub,
                count(*) filter (
                    where financial_status = 'closed'
                ) as closed_count,
                count(*) filter (
                    where financial_status = 'open'
                ) as open_count,
                count(*) filter (
                    where financial_status = 'advance'
                ) as advance_count,
                count(*) filter (
                    where match_status = 'matched'
                ) as matched_count,
                count(distinct customer_id) as customer_count
            from deals
            where source = 'buyers'
            """
        ).fetchone()

    return {
        "payments": {
            **dict(payments),
            "total_amount_rub": money(payments["total_amount_rub"]),
            "internal_amount_rub": money(payments["internal_amount_rub"]),
            "date_from": (
                payments["date_from"].isoformat()
                if payments["date_from"]
                else None
            ),
            "date_to": (
                payments["date_to"].isoformat()
                if payments["date_to"]
                else None
            ),
        },
        "customer_receipts": {
            **dict(receipts),
            "total_amount_rub": money(receipts["total_amount_rub"]),
            "date_from": (
                receipts["date_from"].isoformat()
                if receipts["date_from"]
                else None
            ),
            "date_to": (
                receipts["date_to"].isoformat()
                if receipts["date_to"]
                else None
            ),
        },
        "deals": {
            **dict(deals),
            "total_amount_rub": money(deals["total_amount_rub"]),
            "paid_amount_rub": money(deals["paid_amount_rub"]),
            "open_balance_rub": money(deals["open_balance_rub"]),
        },
    }


@app.get("/api/data/payments")
def loaded_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    search: str | None = Query(default=None, max_length=200),
) -> dict:
    conditions = [
        "p.source = 'payment_battery'",
        "p.direction = 'inflow'",
    ]
    params: list[object] = []
    if search and search.strip():
        term = ilike_term(search.strip())
        conditions.append(
            """
            (
                p.raw_counterparty ilike %s
                or p.description ilike %s
                or p.source_sheet ilike %s
                or exists (
                    select 1
                    from payment_allocations search_pa
                    join deals search_d on search_d.id = search_pa.deal_id
                    join employees search_e on search_e.id = search_d.manager_id
                    where search_pa.payment_id = p.id
                      and search_e.full_name ilike %s
                )
                or exists (
                    select 1
                    from payment_manager_assignments search_assignment
                    where search_assignment.payment_id = p.id
                      and search_assignment.manager_name ilike %s
                )
            )
            """
        )
        params.extend([term, term, term, term, term])

    where_sql = f"where {' and '.join(conditions)}"
    offset = (page - 1) * page_size

    with database() as connection:
        total = connection.execute(
            f"""
            select count(*) as total
            from payments p
            {where_sql}
            """,
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            select
                p.id,
                p.payment_date,
                p.raw_counterparty,
                p.amount_rub,
                p.description,
                p.operation_type,
                p.source_sheet,
                p.source_row,
                p.is_internal_transfer,
                a.name as account_name,
                coalesce(
                    bitrix_employee.full_name,
                    bitrix_assignment.manager_name,
                    managers.manager_name
                ) as manager_name
            from payments p
            join financial_accounts a on a.id = p.account_id
            left join payment_manager_assignments bitrix_assignment
              on bitrix_assignment.payment_id = p.id
            left join employees bitrix_employee
              on bitrix_employee.id = bitrix_assignment.manager_id
            left join lateral (
                select string_agg(
                    distinct e.full_name,
                    ', ' order by e.full_name
                ) as manager_name
                from payment_allocations pa
                join deals d on d.id = pa.deal_id
                join employees e on e.id = d.manager_id
                where pa.payment_id = p.id
            ) managers on true
            {where_sql}
            order by p.payment_date desc, p.id desc
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
                "payment_date": row["payment_date"].isoformat(),
                "amount_rub": money(row["amount_rub"]),
            }
            for row in rows
        ],
    }


@app.get("/api/data/customer-receipts")
def loaded_customer_receipts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    search: str | None = Query(default=None, max_length=200),
) -> dict:
    conditions: list[str] = []
    params: list[object] = []
    if search and search.strip():
        term = ilike_term(search.strip())
        conditions.append(
            """
            (
                customer_name ilike %s
                or document_number ilike %s
                or notes ilike %s
                or manager_name ilike %s
            )
            """
        )
        params.extend([term, term, term, term])

    where_sql = f"where {' and '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    with database() as connection:
        total = connection.execute(
            f"select count(*) as total from customer_receipts {where_sql}",
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            select
                id,
                source_row,
                customer_name,
                document_type,
                document_number,
                document_date,
                paid_amount_rub,
                payment_date,
                manager_name,
                notes
            from customer_receipts
            {where_sql}
            order by payment_date desc, id desc
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
                "document_date": (
                    row["document_date"].isoformat()
                    if row["document_date"]
                    else None
                ),
                "payment_date": row["payment_date"].isoformat(),
                "paid_amount_rub": money(row["paid_amount_rub"]),
            }
            for row in rows
        ],
    }


@app.get("/api/data/deals")
def loaded_deals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    financial_status: Literal["open", "closed", "advance"] | None = None,
    match_status: Literal["unmatched", "matched", "review"] | None = None,
    without_match: bool = False,
    search: str | None = Query(default=None, max_length=200),
    customer: str | None = Query(default=None, max_length=200),
    document_type: str | None = Query(default=None, max_length=100),
    document_number: str | None = Query(default=None, max_length=100),
    manager: str | None = Query(default=None, max_length=200),
    opened_on: str | None = Query(default=None, max_length=20),
    planned_revenue_rub: str | None = Query(default=None, max_length=50),
    paid_amount_rub: str | None = Query(default=None, max_length=50),
    balance_rub: str | None = Query(default=None, max_length=50),
    balance_eur: float | None = None,
    eur_rate: float | None = Query(default=None, gt=0),
) -> dict:
    conditions = ["d.source = 'buyers'"]
    params: list[object] = []
    if financial_status:
        conditions.append("d.financial_status = %s")
        params.append(financial_status)
    if match_status:
        conditions.append("d.match_status = %s")
        params.append(match_status)
    if without_match:
        conditions.append("d.match_status <> 'matched'")
    if search and search.strip():
        term = ilike_term(search.strip())
        conditions.append(
            """
            (
                c.name ilike %s
                or d.original_document_number ilike %s
                or d.title ilike %s
                or e.full_name ilike %s
            )
            """
        )
        params.extend([term, term, term, term])
    column_text_filters = [
        ("c.name", customer),
        ("e.full_name", manager),
        ("d.original_document_type", document_type),
        ("d.original_document_number", document_number),
        ("cast(d.opened_on as text)", opened_on),
    ]
    for expression, value in column_text_filters:
        if value and value.strip():
            conditions.append(f"coalesce({expression}, '') ilike %s")
            params.append(ilike_term(value.strip()))
    numeric_text_filters = [
        ("d.planned_revenue_rub", planned_revenue_rub),
        ("d.paid_amount_rub", paid_amount_rub),
        ("d.balance_rub", balance_rub),
    ]
    for expression, value in numeric_text_filters:
        if value and value.strip():
            normalized = value.replace(" ", "").replace(",", ".").strip()
            conditions.append(f"cast({expression} as text) ilike %s")
            params.append(ilike_term(normalized))
    if balance_eur is not None and eur_rate is not None:
        conditions.append("round(d.balance_rub / %s, 2) = round(%s, 2)")
        params.extend([eur_rate, balance_eur])

    where_sql = f"where {' and '.join(conditions)}"
    offset = (page - 1) * page_size

    with database() as connection:
        total = connection.execute(
            f"""
            select count(*) as total
            from deals d
            join counterparties c on c.id = d.customer_id
            left join employees e on e.id = d.manager_id
            {where_sql}
            """,
            params,
        ).fetchone()["total"]
        totals = connection.execute(
            f"""
            select
                coalesce(sum(d.planned_revenue_rub), 0) as planned_revenue_rub,
                coalesce(sum(d.paid_amount_rub), 0) as paid_amount_rub,
                coalesce(sum(d.balance_rub), 0) as balance_rub
            from deals d
            join counterparties c on c.id = d.customer_id
            left join employees e on e.id = d.manager_id
            {where_sql}
            """,
            params,
        ).fetchone()
        rows = connection.execute(
            f"""
            select
                d.id,
                d.deal_number,
                d.title,
                d.source_row,
                d.original_document_type,
                d.original_document_number,
                d.opened_on,
                d.payment_date,
                d.planned_revenue_rub,
                d.paid_amount_rub,
                d.balance_rub,
                d.financial_status,
                d.match_status,
                c.name as customer_name,
                e.full_name as manager_name,
                link.payment_id,
                link.payment_date as linked_payment_date,
                link.amount_rub as linked_payment_amount_rub
            from deals d
            join counterparties c on c.id = d.customer_id
            left join employees e on e.id = d.manager_id
            left join lateral (
                select
                    p.id as payment_id,
                    p.payment_date,
                    p.amount_rub
                from payment_allocations pa
                join payments p on p.id = pa.payment_id
                where pa.deal_id = d.id
                order by pa.id
                limit 1
            ) link on true
            {where_sql}
            order by d.payment_date desc, d.id desc
            limit %s offset %s
            """,
            [*params, page_size, offset],
        ).fetchall()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "totals": {
            "planned_revenue_rub": money(totals["planned_revenue_rub"]),
            "paid_amount_rub": money(totals["paid_amount_rub"]),
            "balance_rub": money(totals["balance_rub"]),
        },
        "items": [
            {
                **dict(row),
                "opened_on": row["opened_on"].isoformat(),
                "payment_date": (
                    row["payment_date"].isoformat()
                    if row["payment_date"]
                    else None
                ),
                "planned_revenue_rub": money(
                    row["planned_revenue_rub"]
                ),
                "paid_amount_rub": money(row["paid_amount_rub"]),
                "balance_rub": money(row["balance_rub"]),
                "linked_payment_date": (
                    row["linked_payment_date"].isoformat()
                    if row["linked_payment_date"]
                    else None
                ),
                "linked_payment_amount_rub": (
                    money(row["linked_payment_amount_rub"])
                    if row["linked_payment_amount_rub"] is not None
                    else None
                ),
            }
            for row in rows
        ],
    }


@app.get("/api/data/deals/{deal_id}/payments")
def loaded_deal_payments(deal_id: int) -> dict:
    with database() as connection:
        deal = connection.execute(
            """
            select id, deal_number, title
            from deals
            where id = %(deal_id)s and source = 'buyers'
            """,
            {"deal_id": deal_id},
        ).fetchone()
        if deal is None:
            return {"deal": None, "items": []}

        rows = connection.execute(
            """
            select
                p.id,
                p.payment_date,
                p.amount_rub,
                p.raw_counterparty,
                p.description,
                p.source_sheet,
                p.source_row,
                a.name as account_name,
                pa.allocated_amount_rub,
                pa.match_confidence
            from payment_allocations pa
            join payments p on p.id = pa.payment_id
            join financial_accounts a on a.id = p.account_id
            where pa.deal_id = %(deal_id)s
            order by p.payment_date, p.id
            """,
            {"deal_id": deal_id},
        ).fetchall()

    return {
        "deal": dict(deal),
        "items": [
            {
                **dict(row),
                "payment_date": row["payment_date"].isoformat(),
                "amount_rub": money(row["amount_rub"]),
                "allocated_amount_rub": money(
                    row["allocated_amount_rub"]
                ),
            }
            for row in rows
        ],
    }


@app.get("/api/data/client-aliases")
def payment_client_aliases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
) -> dict:
    offset = (page - 1) * page_size
    with database() as connection:
        total = connection.execute(
            "select count(*) as total from payment_client_aliases"
        ).fetchone()["total"]
        rows = connection.execute(
            """
            select a.id, a.payment_name, c.name as buyer_name, a.status,
                   a.evidence_type, a.evidence_count
            from payment_client_aliases a
            join counterparties c on c.id = a.counterparty_id
            order by a.status, a.evidence_count desc, a.payment_name
            limit %s offset %s
            """,
            [page_size, offset],
        ).fetchall()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [dict(row) for row in rows],
    }


@app.get("/api/data/payments/available")
def available_payments_for_allocation(
    search: str | None = Query(default=None, max_length=200),
    page_size: int = Query(default=500, ge=10, le=500),
) -> dict:
    conditions = [
        "p.source = 'payment_battery'",
        "p.direction = 'inflow'",
    ]
    params: list[object] = []
    if search and search.strip():
        term = ilike_term(search.strip())
        conditions.append(
            "(p.raw_counterparty ilike %s or p.description ilike %s)"
        )
        params.extend([term, term])

    with database() as connection:
        rows = connection.execute(
            f"""
            select
                p.id,
                p.payment_date,
                p.amount_rub,
                p.raw_counterparty,
                p.description,
                a.name as account_name,
                coalesce(sum(pa.allocated_amount_rub), 0) as allocated_amount_rub,
                p.amount_rub - coalesce(sum(pa.allocated_amount_rub), 0) as available_amount_rub
            from payments p
            join financial_accounts a on a.id = p.account_id
            left join payment_allocations pa on pa.payment_id = p.id
            where {' and '.join(conditions)}
            group by p.id, a.name
            having p.amount_rub - coalesce(sum(pa.allocated_amount_rub), 0) > 0
            order by p.payment_date desc, p.id desc
            limit %s
            """,
            [*params, page_size],
        ).fetchall()

    return {
        "items": [
            {
                **dict(row),
                "payment_date": row["payment_date"].isoformat(),
                "amount_rub": money(row["amount_rub"]),
                "allocated_amount_rub": money(row["allocated_amount_rub"]),
                "available_amount_rub": money(row["available_amount_rub"]),
            }
            for row in rows
        ]
    }


@app.get("/api/data/deals/{deal_id}/candidate-payments")
def deal_candidate_payments(deal_id: int) -> dict:
    with database() as connection:
        deal = connection.execute(
            """
            select d.id, c.id as counterparty_id, c.name as customer_name
            from deals d
            join counterparties c on c.id = d.customer_id
            where d.id = %s and d.source = 'buyers'
            """,
            [deal_id],
        ).fetchone()
        if deal is None:
            return {"items": []}

        rows = connection.execute(
            """
            select
                p.id,
                p.payment_date,
                p.amount_rub,
                p.raw_counterparty,
                p.description,
                p.source_sheet,
                p.source_row,
                a.name as account_name,
                coalesce(sum(pa.allocated_amount_rub), 0) as allocated_amount_rub,
                p.amount_rub - coalesce(sum(pa.allocated_amount_rub), 0) as available_amount_rub
            from payments p
            join financial_accounts a on a.id = p.account_id
            left join payment_allocations pa on pa.payment_id = p.id
            where p.source = 'payment_battery'
              and p.direction = 'inflow'
              and not exists (
                select 1 from payment_allocations current_link
                where current_link.payment_id = p.id and current_link.deal_id = %(deal_id)s
              )
              and coalesce(p.raw_counterparty, '') <> ''
              and (
                position(
                  regexp_replace(lower(%(customer_name)s), '[^[:alnum:]]', '', 'g')
                  in regexp_replace(lower(p.raw_counterparty), '[^[:alnum:]]', '', 'g')
                ) > 0
                or position(
                  regexp_replace(lower(p.raw_counterparty), '[^[:alnum:]]', '', 'g')
                  in regexp_replace(lower(%(customer_name)s), '[^[:alnum:]]', '', 'g')
                ) > 0
                or exists (
                  select 1
                  from payment_client_aliases alias
                  where alias.counterparty_id = %(counterparty_id)s
                    and alias.status = 'confirmed'
                    and lower(alias.payment_name) = lower(p.raw_counterparty)
                )
              )
            group by p.id, a.name
            having p.amount_rub - coalesce(sum(pa.allocated_amount_rub), 0) > 0
            order by p.payment_date desc, p.id desc
            """,
            {
                "deal_id": deal_id,
                "counterparty_id": deal["counterparty_id"],
                "customer_name": deal["customer_name"],
            },
        ).fetchall()

    return {
        "items": [
            {
                **dict(row),
                "payment_date": row["payment_date"].isoformat(),
                "amount_rub": money(row["amount_rub"]),
                "allocated_amount_rub": money(row["allocated_amount_rub"]),
                "available_amount_rub": money(row["available_amount_rub"]),
            }
            for row in rows
        ]
    }


@app.post("/api/data/deals/{deal_id}/payments")
def create_manual_payment_allocation(
    deal_id: int, payload: ManualPaymentAllocationCreate
) -> dict:
    if payload.allocated_amount_rub <= 0:
        raise HTTPException(status_code=422, detail="Сумма распределения должна быть больше нуля.")

    with database() as connection:
        deal = connection.execute(
            "select id from deals where id = %s and source = 'buyers'",
            [deal_id],
        ).fetchone()
        if deal is None:
            raise HTTPException(status_code=404, detail="Сделка не найдена.")

        payment = connection.execute(
            """
            select id, amount_rub
            from payments
            where id = %s and source = 'payment_battery' and direction = 'inflow'
            for update
            """,
            [payload.payment_id],
        ).fetchone()
        if payment is None:
            raise HTTPException(status_code=404, detail="Входящий платёж не найден.")

        existing = connection.execute(
            "select id from payment_allocations where payment_id = %s and deal_id = %s",
            [payload.payment_id, deal_id],
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Этот платёж уже связан со сделкой.")

        allocated = connection.execute(
            "select coalesce(sum(allocated_amount_rub), 0) as amount from payment_allocations where payment_id = %s",
            [payload.payment_id],
        ).fetchone()["amount"]
        available = payment["amount_rub"] - allocated
        if payload.allocated_amount_rub > available:
            raise HTTPException(
                status_code=422,
                detail=f"Доступно для распределения: {money(available)} ₽.",
            )

        allocation = connection.execute(
            """
            insert into payment_allocations (
                payment_id, deal_id, allocated_amount_rub, source, match_confidence
            ) values (%s, %s, %s, 'manual', 'manual')
            returning id
            """,
            [payload.payment_id, deal_id, payload.allocated_amount_rub],
        ).fetchone()
        connection.execute(
            "update deals set match_status = 'matched' where id = %s",
            [deal_id],
        )

    return {"id": allocation["id"], "available_amount_rub": money(available - payload.allocated_amount_rub)}


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
        term = ilike_term(search.strip())
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
