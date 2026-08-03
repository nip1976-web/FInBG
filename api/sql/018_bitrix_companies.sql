-- Компании Bitrix с их реквизитами: ИНН, КПП, юридическое название.
--
-- Зачем. Сегодня клиент опознаётся по написанию названия, и это уже дважды
-- стоило нам ошибок: «Приангарский ЛПК» и «Приангарский Лесоперерабатывающий
-- Комплекс» — одна фирма, но для программы разные. При перезаливке файла
-- переименование одного клиента обрывает все его сделки разом.
--
-- ИНН такого не допускает: он не зависит от кавычек, порядка слов и того, где
-- стоит «ООО». Одна фирма — один ИНН.
--
-- Где он лежит в Bitrix: у счёта ИНН нет, у карточки компании отдельного поля
-- тоже нет. Он живёт в разделе реквизитов, отдельным запросом: счёт → компания
-- → реквизиты → ИНН. Заполнен у 109 компаний из 110.
--
-- Таблица отдельная, а не поля в counterparties: это данные Bitrix, они
-- обновляются своим расписанием и при перезаливке Buyers.xlsx не должны
-- ни теряться, ни мешаться. Связь с нашими контрагентами устанавливается
-- отдельно и осознанно, а не приравниванием названий.

create table if not exists bitrix_companies (
    bitrix_company_id bigint primary key,
    title             text,
    legal_name        text,
    inn               text,
    kpp               text,
    fetched_at        timestamptz not null default now()
);

comment on table bitrix_companies is
    'Компании Bitrix и их реквизиты. Наполняется sync_bitrix_companies.py.';
comment on column bitrix_companies.title is
    'Название карточки в Bitrix — то, что видно в интерфейсе';
comment on column bitrix_companies.legal_name is
    'Юридическое название из реквизитов — то, что стоит в платёжках';
comment on column bitrix_companies.inn is
    'ИНН: 10 цифр у юрлица, 12 у ИП. Не уникален: на одну фирму бывает две карточки';

create index if not exists bitrix_companies_inn_idx
    on bitrix_companies (inn)
    where inn is not null;
