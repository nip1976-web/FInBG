begin;

-- Payer names that a human has confirmed refer to a given Bitrix company even
-- though the two spellings don't look alike ("С-ДОК АО" in the bank statement
-- vs "Сокольский Деревообрабатывающий Комбинат" in Bitrix). Without this the
-- counterparty check in api/app/counterparties.py would send every such
-- payment to manual review, over and over.
create table if not exists bitrix_company_links (
    id bigint primary key generated always as identity,
    payer_name text not null,
    bitrix_company_id bigint not null,
    bitrix_company_name text,
    created_at timestamptz not null default now()
);

create unique index if not exists bitrix_company_links_uidx
    on bitrix_company_links(lower(payer_name), bitrix_company_id);

commit;
