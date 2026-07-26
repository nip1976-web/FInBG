\pset format unaligned
\pset fieldsep '|'
\pset tuples_only on

select
    'payments',
    count(*),
    coalesce(sum(amount_rub), 0),
    count(*) filter (where direction <> 'inflow'),
    count(*) filter (where counterparty_id is not null)
from payments
where source = 'payment_battery';

select
    'customer_receipts',
    count(*),
    coalesce(sum(paid_amount_rub), 0)
from customer_receipts;

select
    'payment_allocations',
    count(*)
from payment_allocations;

select
    'payments_by_sheet',
    source_sheet,
    count(*),
    coalesce(sum(amount_rub), 0)
from payments
where source = 'payment_battery'
group by source_sheet
order by source_sheet;

select
    'customer_receipts_period',
    min(payment_date),
    max(payment_date),
    count(distinct customer_name)
from customer_receipts;
