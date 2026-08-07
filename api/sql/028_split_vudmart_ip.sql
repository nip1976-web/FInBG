-- Отделяет ИП Кожухова от ООО «ВУДМАРТ».
--
-- Под именем «Вудмарт ООО» в базе смешались две разные фирмы: само ООО с ИНН
-- 1001327191 и индивидуальный предприниматель Кожухов В. К. с ИНН
-- 102000110137. Нашлось это, когда счета клиента запросили в Bitrix: пять
-- указывали на одну компанию, счёт № 893 — на другую.
--
-- 07.08.2026 Николай разделил их в файле: строка со счётом 893 записана на
-- «Кожухов Василий Константинович (ИП)».
--
-- Почему одной перезаливкой не обойтись. В паспорт сделки входит покупатель.
-- Смена имени в файле означает новый паспорт: загрузчик завёл бы новую сделку,
-- а старая осталась бы в стороне с привязанным платежом на 26 000 ₽. Поэтому
-- переносим сделку сами и пересчитываем паспорт — тогда платёж остаётся на ней.
--
-- Имя карточки берём ровно как в файле: названия пишутся для чтения людьми, а
-- опознание идёт по нормализованному виду и по ИНН, не по написанию.

begin;

insert into counterparties (name, counterparty_type, default_currency, tax_id, legal_name)
select
    'Кожухов Василий Константинович (ИП)',
    'customer',
    'RUB',
    bc.inn,
    bc.legal_name
from bitrix_companies bc
where bc.inn = '102000110137'
  and not exists (
      select 1 from counterparties c where c.tax_id = '102000110137'
  );

update deals d
set customer_id = (select id from counterparties where tax_id = '102000110137'),
    deal_number = buyers_deal_number(
        (select id from counterparties where tax_id = '102000110137'),
        d.original_document_type,
        d.original_document_number,
        d.source_payload->>'documentDate',
        d.source_payload->>'paymentDate',
        d.source_payload->>'dealAmount'
    ),
    updated_at = now()
where d.id = 40
  and d.original_document_number = '893';

-- Предложение по «Вудмарт ООО» больше не спорное: после разделения все его
-- счета указывают на одну компанию.
update counterparty_tax_id_suggestions
set status = 'confirmed', decided_at = now()
where suggested_tax_id = '1001327191'
  and status = 'suggested';

commit;
