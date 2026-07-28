begin;

-- Counterparties that legitimately never quote our Bitrix invoice number:
-- marketplaces (Ozon) and other channels quote their own internal order id,
-- so flagging them as "invoice not confirmed by Bitrix" is pure noise.
-- Matched on payments.raw_counterparty because payments.counterparty_id is
-- not populated by the Excel import.
create table if not exists bitrix_match_exclusions (
    id bigint primary key generated always as identity,
    counterparty_pattern text not null,
    note text,
    created_at timestamptz not null default now()
);

create unique index if not exists bitrix_match_exclusions_pattern_uidx
    on bitrix_match_exclusions(lower(counterparty_pattern));

insert into bitrix_match_exclusions (counterparty_pattern, note)
values ('Интернет Решения', 'Ozon: платит по своему номеру заказа, учитывается как отдельная продажа')
on conflict do nothing;

commit;
