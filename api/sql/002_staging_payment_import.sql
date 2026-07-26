begin;

alter table import_batches
    add column if not exists source_path text,
    add column if not exists source_size bigint,
    add column if not exists source_modified_at timestamptz,
    add column if not exists period_from date,
    add column if not exists content_hash char(64);

create table if not exists staging_payment_operations (
    id bigint generated always as identity primary key,
    import_batch_id bigint not null references import_batches(id),
    source_file text not null,
    source_sheet text not null,
    source_row integer not null check (source_row > 1),
    source_record_key char(64) not null unique,
    content_hash char(64) not null,
    direction text not null check (direction in ('inflow', 'outflow')),
    sequence_number text,
    account_code text,
    counterparty text,
    amount_rub numeric(18,2) not null check (amount_rub > 0),
    operation_date date not null,
    raw_date text,
    explanation text,
    payment_basis text,
    client text,
    category text,
    reporting_period date,
    plan_code text,
    movement_type text,
    manager text,
    revenue_type text,
    likely_internal_transfer boolean not null default false,
    possible_duplicate boolean not null default false,
    validation_status text not null default 'pending'
        check (validation_status in ('pending', 'ready', 'excluded', 'error', 'imported')),
    validation_errors jsonb not null default '[]'::jsonb,
    source_payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists staging_payment_batch_idx
    on staging_payment_operations(import_batch_id);
create index if not exists staging_payment_date_idx
    on staging_payment_operations(operation_date);
create index if not exists staging_payment_sheet_row_idx
    on staging_payment_operations(source_sheet, source_row);
create index if not exists staging_payment_status_idx
    on staging_payment_operations(validation_status);
create index if not exists staging_payment_internal_idx
    on staging_payment_operations(likely_internal_transfer)
    where likely_internal_transfer;
create index if not exists staging_payment_duplicate_idx
    on staging_payment_operations(possible_duplicate)
    where possible_duplicate;

commit;
