-- Сводит две карточки Приангарского в одну и проставляет ИНН.
--
-- Николай подтвердил 03.08.2026: «Приангарский Лесоперерабатывающий Комплекс
-- ООО» и «Приангарский ЛПК ООО» — один и тот же клиент, ИНН 2463223960.
-- «Пошехонский Лесоперерабатывающий Комплекс», который нашёлся поиском по
-- названию, — другой клиент, предложение отклоняем.
--
-- Оставляем карточку «Приангарский ЛПК ООО»: так фирма называется и в
-- реквизитах Bitrix, и в платёжках. Сделка со второй карточки переезжает сюда.
--
-- Важное про паспорт сделки: он включает код покупателя, поэтому у переехавшей
-- сделки его надо пересчитать. Иначе при следующей перезаливке паспорт не
-- совпадёт, сделка заведётся заново, а старая останется в стороне.
--
-- Соответствие названия плательщика, заведённое 31.07.2026, после слияния
-- теряет смысл: карточка теперь называется ровно так же, как плательщик в
-- выписке, и совпадение находится напрямую. Удаляем, чтобы не путало.

begin;

-- 1. решения по предложениям
update counterparty_tax_id_suggestions
set status = 'rejected', decided_at = now()
where suggested_tax_id = '7610132988';  -- Пошехонский: другая фирма

update counterparty_tax_id_suggestions
set status = 'confirmed', decided_at = now()
where suggested_tax_id = '2463223960';  -- Приангарский ЛПК: та самая

-- 2. сделки переезжают на остающуюся карточку, с пересчётом паспорта
update deals d
set customer_id = 46,
    deal_number = buyers_deal_number(
        46,
        d.source_payload->>'documentType',
        d.source_payload->>'documentNumber',
        d.source_payload->>'documentDate',
        d.source_payload->>'paymentDate',
        d.source_payload->>'dealAmount'
    ),
    updated_at = now()
where d.customer_id = 1;

-- 3. соответствие названия больше не нужно
delete from payment_client_aliases
where counterparty_id = 1;

-- 4. предложения, висевшие на исчезающей карточке
delete from counterparty_tax_id_suggestions
where counterparty_id = 1;

-- 5. ИНН и реквизиты на остающуюся карточку.
--
-- Реквизиты берём из самого предложения, а не из bitrix_companies: там лежат
-- только компании, на которые ссылаются наши счета, а у Приангарского счетов
-- нет — одни спецификации. Появятся счета — компания подтянется расписанием
-- и сама заполнит недостающее.
update counterparties c
set tax_id = '2463223960',
    legal_name = coalesce(
        c.legal_name,
        (
            select s.legal_name
            from counterparty_tax_id_suggestions s
            where s.suggested_tax_id = '2463223960'
              and s.legal_name is not null
            limit 1
        )
    )
where c.id = 46;

-- 6. пустая карточка уходит
delete from counterparties where id = 1;

commit;
