begin;

create table if not exists payment_client_aliases (
    id bigint generated always as identity primary key,
    payment_name text not null,
    counterparty_id bigint not null references counterparties(id) on delete cascade,
    status text not null default 'suggested'
        check (status in ('suggested', 'confirmed', 'rejected')),
    evidence_type text not null,
    evidence_count integer not null default 1 check (evidence_count > 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists payment_client_aliases_unique
    on payment_client_aliases (lower(payment_name), counterparty_id);

commit;
