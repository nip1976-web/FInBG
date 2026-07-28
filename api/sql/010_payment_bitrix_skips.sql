begin;

-- One-off payments whose счёт genuinely isn't in Bitrix: invoices a manager
-- issued by hand, documents predating Bitrix, and so on. Distinct from
-- bitrix_match_exclusions, which silences a counterparty wholesale — here the
-- rest of that counterparty's payments keep being checked.
create table if not exists payment_bitrix_skips (
    payment_id bigint primary key references payments(id) on delete cascade,
    note text,
    created_at timestamptz not null default now()
);

commit;
