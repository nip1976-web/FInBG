begin;

alter table payments
    add column if not exists raw_counterparty text,
    add column if not exists source_sheet text,
    add column if not exists source_row integer,
    add column if not exists is_internal_transfer boolean not null default false,
    add column if not exists source_payload jsonb;

create table if not exists customer_receipts (
    id bigint generated always as identity primary key,
    import_batch_id bigint references import_batches(id),
    source_file text not null,
    source_sheet text not null default 'СчетаРуб',
    source_row integer not null check (source_row > 3),
    source_record_key char(64) not null unique,
    customer_name text not null,
    document_type text,
    document_number text,
    document_date date,
    paid_amount_rub numeric(18,2) not null check (paid_amount_rub > 0),
    payment_date date not null,
    manager_name text,
    notes text,
    source_payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists customer_receipts_payment_date_idx
    on customer_receipts(payment_date);
create index if not exists customer_receipts_customer_idx
    on customer_receipts(customer_name);
create index if not exists customer_receipts_document_idx
    on customer_receipts(document_type, document_number);

commit;
