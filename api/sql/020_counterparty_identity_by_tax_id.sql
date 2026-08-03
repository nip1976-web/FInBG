-- Делает ИНН ключевым признаком клиента в нашем справочнике.
--
-- Отдельную таблицу клиентов не заводим намеренно: справочник уже есть — это
-- counterparties, на него ссылаются сделки, счета и соответствия названий.
-- Второй справочник означал бы два источника истины, которые однажды разойдутся.
-- Поэтому усиливаем существующий.
--
-- Что меняется:
--
--   * ограничение «один ИНН — одна карточка» на уровне базы. Раньше ничто не
--     мешало завести двух клиентов с одним ИНН, и мы это уже проходили с
--     Приангарским, где одна фирма записана двумя карточками;
--   * рядом с ИНН — КПП и юридическое название из реквизитов Bitrix: то, как
--     клиент называется в платёжках, а не только как записан у нас.
--
-- Ограничение частичное: клиенты без ИНН законны. Частные лица и платёжные
-- системы (Юмани) ИНН в нашем обороте не имеют, и это не пробел в данных.

begin;

alter table counterparties
    add column if not exists legal_name text,
    add column if not exists kpp text;

comment on column counterparties.tax_id is
    'ИНН — ключевой признак клиента. Не зависит от написания названия';
comment on column counterparties.legal_name is
    'Юридическое название из реквизитов Bitrix — то, что стоит в платёжках';

-- пробелы по краям сделали бы два одинаковых ИНН разными для ограничения
update counterparties
set tax_id = nullif(btrim(tax_id), '')
where tax_id is distinct from nullif(btrim(tax_id), '');

create unique index if not exists counterparties_tax_id_uidx
    on counterparties (tax_id)
    where tax_id is not null;

-- КПП и юридическое название подтягиваем по уже проставленному ИНН.
-- На один ИНН в Bitrix бывает две карточки — берём любую: реквизиты у них
-- одни и те же, различаются только названия карточек.
update counterparties c
set legal_name = source.legal_name,
    kpp = source.kpp
from (
    select distinct on (inn) inn, legal_name, kpp
    from bitrix_companies
    where inn is not null
    order by inn, bitrix_company_id
) source
where c.tax_id = source.inn
  and (c.legal_name is null or c.kpp is null);

commit;
