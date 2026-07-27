begin;

alter table employees
    add column if not exists bitrix_user_id bigint;

create unique index if not exists employees_bitrix_user_id_uidx
    on employees(bitrix_user_id)
    where bitrix_user_id is not null;

create table if not exists payment_manager_assignments (
    payment_id bigint primary key references payments(id) on delete cascade,
    manager_name text not null,
    document_number text not null,
    bitrix_invoice_id bigint not null,
    source text not null default 'bitrix_invoice',
    matched_at timestamptz not null default now()
);

create index if not exists payment_manager_assignments_manager_idx
    on payment_manager_assignments(lower(manager_name));

commit;
