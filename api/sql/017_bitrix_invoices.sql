-- Счета из Bitrix: валюта и сумма в валюте счёта.
--
-- Зачем отдельная таблица, а не поля в deals: это данные Bitrix, а не нашей
-- выгрузки из Excel. Они обновляются своим расписанием, живут своей жизнью и
-- при перезаливке Buyers.xlsx не должны ни теряться, ни мешаться.
--
-- Связь со сделкой — по номеру документа: в этом Bitrix номер счёта совпадает с
-- внутренним кодом записи (проверено на 217 счетах из 218). Внешний номер вида
-- «2026/05/21/3643» хранится рядом справочно, для сверки глазами.
--
-- Валюта счёта — то, ради чего всё затевалось: рублёвый счёт считаем в рублях,
-- валютный в валюте, а оплату пересчитываем по курсу дня платежа.

create table if not exists bitrix_invoices (
    bitrix_invoice_id bigint primary key,
    account_number    text,
    currency          character(3) not null,
    amount            numeric(18, 2) not null check (amount >= 0),
    company_id        bigint,
    fetched_at        timestamptz not null default now()
);

comment on table bitrix_invoices is
    'Счета Bitrix: валюта и сумма. Наполняется sync_bitrix_invoices.py.';
comment on column bitrix_invoices.bitrix_invoice_id is
    'Код записи в Bitrix, он же номер счёта в наших сделках';
comment on column bitrix_invoices.amount is
    'Сумма счёта в валюте счёта, не в рублях';

create index if not exists bitrix_invoices_currency_idx
    on bitrix_invoices (currency);
