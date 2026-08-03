-- Предложения ИНН для клиентов, до которых не дотянуться через счета.
--
-- Почему не проставлять сразу. Таких клиентов ищут в Bitrix по названию, а
-- поиск по названию врёт. Проверено на живых данных: «Приангарский
-- Лесоперерабатывающий Комплекс» нашёл «ООО ПОШЕХОНСКИЙ ЛЕСОПЕРЕРАБАТЫВАЮЩИЙ
-- КОМПЛЕКС» — совпало длинное слово, а фирма другая, из другой области.
-- Проставь такой ИНН автоматически — и сделка на 5,3 млн ₽ уехала бы чужому
-- клиенту незаметно.
--
-- Поэтому здесь только предложения. Решение принимает человек: подтвердил —
-- ИНН переносится в карточку клиента, отклонил — предложение остаётся в
-- истории, чтобы не всплывать снова при каждом запуске.
--
-- Тот же порядок, что и у соответствий названий плательщиков: сначала
-- предложение, потом подтверждение, и никогда наоборот.

create table if not exists counterparty_tax_id_suggestions (
    id                bigint generated always as identity primary key,
    counterparty_id   bigint not null references counterparties (id) on delete cascade,
    suggested_tax_id  text not null,
    bitrix_company_id bigint,
    bitrix_title      text,
    legal_name        text,
    match_reason      text not null,
    status            text not null default 'suggested'
                      check (status in ('suggested', 'confirmed', 'rejected')),
    created_at        timestamptz not null default now(),
    decided_at        timestamptz
);

create unique index if not exists counterparty_tax_id_suggestions_uidx
    on counterparty_tax_id_suggestions (counterparty_id, suggested_tax_id);

comment on table counterparty_tax_id_suggestions is
    'Предложения ИНН на подтверждение. Автоматически в карточку не попадают.';
comment on column counterparty_tax_id_suggestions.match_reason is
    'Чем обосновано предложение: name_search — найдено поиском по названию';
