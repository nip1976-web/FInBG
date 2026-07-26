"""Create review-only payment-client alias suggestions from exact evidence."""

import os
import sys

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL", "dbname=finbg")
def main() -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                with evidence as (
                    select p.raw_counterparty as payment_name, d.customer_id
                    from deals d
                    join payments p
                      on p.payment_date = d.payment_date
                     and p.amount_rub = d.paid_amount_rub
                     and p.source = 'payment_battery'
                     and p.direction = 'inflow'
                    where d.source = 'buyers'
                      and d.financial_status = 'closed'
                      and d.paid_amount_rub > 0
                      and p.raw_counterparty is not null
                      and not exists (
                        select 1 from payment_allocations pa where pa.deal_id = d.id
                      )
                ),
                unambiguous_names as (
                    select payment_name
                    from evidence
                    group by payment_name
                    having count(distinct customer_id) = 1
                ),
                suggestions as (
                    select e.payment_name, e.customer_id, count(*)::integer as evidence_count
                    from evidence e
                    join unambiguous_names u using (payment_name)
                    group by e.payment_name, e.customer_id
                )
                insert into payment_client_aliases (
                    payment_name, counterparty_id, status, evidence_type, evidence_count
                )
                select payment_name, customer_id, 'suggested', 'same_date_and_amount', evidence_count
                from suggestions
                on conflict (lower(payment_name), counterparty_id) do update
                  set evidence_count = excluded.evidence_count,
                      updated_at = now()
                returning id
                """
            )
            print(f"Suggested aliases created or refreshed: {cursor.rowcount}")


if __name__ == "__main__":
    sys.exit(main())
