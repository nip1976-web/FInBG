-- Проставляет нашим контрагентам ИНН, взятый из Bitrix.
--
-- Путь до ИНН: сделка → номер счёта → счёт Bitrix → компания → реквизиты.
-- Прямой связи «наш клиент ↔ компания Bitrix» нет, поэтому идём через счета:
-- по ним видно, какой компании Bitrix принадлежат сделки клиента.
--
-- Записываем только когда все счета клиента указывают на один ИНН. Разошлись —
-- не трогаем: значит под одним названием у нас смешаны две фирмы, и это надо
-- разбирать глазами, а не записывать наугад.
--
-- Две карточки Bitrix с одним ИНН помехой не являются: «АО С-ДОК» и
-- «Сокольский Деревообрабатывающий Комбинат» — одна фирма, ИНН у них общий,
-- поэтому клиент всё равно получает однозначный ответ.
--
-- Ничего не удаляет и не перезаписывает уже заполненное.

begin;

create temporary table tax_id_candidates on commit drop as
select
    c.id as counterparty_id,
    c.name as counterparty_name,
    min(bc.inn) as inn,
    count(distinct bc.inn) as distinct_inn_count
from counterparties c
join deals d on d.customer_id = c.id
join bitrix_invoices bi on bi.bitrix_invoice_id = case
    when d.original_document_type = 'счет'
     and d.original_document_number ~ '^[0-9]+$'
    then d.original_document_number::bigint
end
join bitrix_companies bc on bc.bitrix_company_id = bi.company_id
where bc.inn is not null
  and c.tax_id is null
group by c.id, c.name;

update counterparties c
set tax_id = t.inn
from tax_id_candidates t
where c.id = t.counterparty_id
  and t.distinct_inn_count = 1;

commit;
