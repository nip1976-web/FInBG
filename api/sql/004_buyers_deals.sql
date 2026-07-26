begin;

alter table deals
    add column if not exists source text,
    add column if not exists source_file text,
    add column if not exists source_sheet text,
    add column if not exists source_row integer,
    add column if not exists original_document_type text,
    add column if not exists original_document_number text,
    add column if not exists paid_amount_rub numeric(18,2) not null default 0,
    add column if not exists balance_rub numeric(18,2) not null default 0,
    add column if not exists payment_date date,
    add column if not exists financial_status text
        check (financial_status in ('open', 'closed', 'advance')),
    add column if not exists match_status text not null default 'unmatched'
        check (match_status in ('unmatched', 'matched', 'review')),
    add column if not exists source_payload jsonb;

create unique index if not exists deals_source_row_uidx
    on deals(source, source_row)
    where source is not null and source_row is not null;

create index if not exists deals_financial_status_idx
    on deals(financial_status);
create index if not exists deals_match_status_idx
    on deals(match_status);

alter table payment_allocations
    add column if not exists source text,
    add column if not exists match_confidence text
        check (match_confidence in ('automatic', 'manual'));

create unique index if not exists payment_allocations_payment_deal_uidx
    on payment_allocations(payment_id, deal_id);

commit;
