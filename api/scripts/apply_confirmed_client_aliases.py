"""Confirm reviewed aliases and allocate only unambiguous full payments."""

import os

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "dbname=finbg")

PAIR_SQL = """
with pairs as (
    select p.id as payment_id, d.id as deal_id, p.amount_rub,
           p.raw_counterparty, d.deal_number, d.original_document_number,
           count(*) over (partition by p.id) as payment_candidates,
           count(*) over (partition by d.id) as deal_candidates
    from payment_client_aliases a
    join payments p on lower(p.raw_counterparty) = lower(a.payment_name)
    join deals d on d.customer_id = a.counterparty_id
               and d.payment_date = p.payment_date
               and d.paid_amount_rub = p.amount_rub
    where a.status = 'confirmed'
      and p.source = 'payment_battery' and p.direction = 'inflow'
      and d.source = 'buyers'
      and not exists (select 1 from payment_allocations x where x.payment_id = p.id)
      and not exists (select 1 from payment_allocations x where x.deal_id = d.id)
)
select * from pairs
where payment_candidates = 1 and deal_candidates = 1
order by payment_id
"""


def main() -> None:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("update payment_client_aliases set status = 'confirmed', updated_at = now() where status = 'suggested'")
            cursor.execute(PAIR_SQL)
            pairs = cursor.fetchall()
            for pair in pairs:
                cursor.execute(
                    """
                    insert into payment_allocations (
                        payment_id, deal_id, allocated_amount_rub, source, match_confidence
                    ) values (%(payment_id)s, %(deal_id)s, %(amount_rub)s,
                              'confirmed_client_alias', 'manual')
                    """,
                    pair,
                )
                cursor.execute("update deals set match_status = 'matched' where id = %s", [pair["deal_id"]])
                print(f"{pair['raw_counterparty']} -> {pair['deal_number']} / {pair['original_document_number']}: {pair['amount_rub']}")
            print(f"Confirmed aliases: all suggested. Allocations created: {len(pairs)}")


if __name__ == "__main__":
    main()
