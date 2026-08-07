-- Дописывает сделке ЛДК № 3 дату документа, которой раньше не было.
--
-- В паспорт сделки входит дата документа. У счёта 2919 она в файле пустовала,
-- и паспорт получился с пустым хвостом: BUYERS-72-счет-2919-. Николай дату
-- заполнил — 31.03.2026, — и файл стал давать другой паспорт. Для программы
-- это была бы новая сделка, а старая осталась бы в стороне с привязанным
-- платежом на 44 072,50 ₽.
--
-- Сомнений в том, что это один документ, нет: клиент, номер счёта, сумма и дата
-- оплаты совпадают до знака, отличалось только пустое поле. Поэтому дописываем
-- дату в запись и пересчитываем паспорт — сделка остаётся той же, платёж
-- остаётся на ней.
--
-- Правка точечная, а не правило: «пустое поле заполнилось» и «поле изменили» с
-- точки зрения данных выглядят одинаково, и разбирать такое надо поштучно.

begin;

update deals
set source_payload = jsonb_set(
        source_payload, '{documentDate}', '"2026-03-31"'::jsonb, true
    ),
    updated_at = now()
where id = 129
  and original_document_number = '2919'
  and coalesce(source_payload->>'documentDate', '') = '';

update deals d
set deal_number = buyers_deal_number(
        d.customer_id,
        d.original_document_type,
        d.original_document_number,
        d.source_payload->>'documentDate',
        d.source_payload->>'paymentDate',
        d.source_payload->>'dealAmount'
    ),
    updated_at = now()
where d.id = 129;

commit;
