begin;

-- Manual corrections of the счёт/спецификация reference parsed out of the
-- payment description. Kept in its own table rather than on `payments` so a
-- re-import from Excel (which upserts payments by source_row) can't wipe them.
create table if not exists payment_document_overrides (
    payment_id bigint primary key references payments(id) on delete cascade,
    document_kind text not null check (document_kind in ('invoice', 'spec')),
    document_number text not null,
    updated_at timestamptz not null default now()
);

create index if not exists payment_document_overrides_number_idx
    on payment_document_overrides(document_number);

commit;
