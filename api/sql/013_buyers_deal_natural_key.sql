-- Сделка из выгрузки Buyers опознаётся по документу, а не по номеру строки Excel.
-- Раньше deal_number = 'BUYERS-<номер строки>', поэтому удаление или вставка строки
-- в файле сдвигала все строки ниже и при переимпорте данные приезжали в чужие сделки.

create or replace function buyers_deal_number(
    p_customer_id bigint,
    p_document_type text,
    p_document_number text,
    p_document_date text,
    p_payment_date text,
    p_deal_amount text
) returns text
language sql
immutable
as $$
    select case
        when coalesce(btrim(p_document_number), '') <> '' then
            'BUYERS-' || p_customer_id
            || '-' || lower(coalesce(btrim(p_document_type), ''))
            || '-' || lower(btrim(p_document_number))
            || '-' || coalesce(btrim(p_document_date), '')
        else
            -- Ozon и физлица приходят без номера документа: их различают
            -- дата оплаты и сумма.
            'BUYERS-' || p_customer_id
            || '-' || coalesce(btrim(p_payment_date), '')
            || '-' || to_char(
                   coalesce(nullif(btrim(p_deal_amount), ''), '0')::numeric,
                   'FM99999999999990.00'
               )
    end;
$$;

-- Номер строки больше не признак сделки: одна и та же сделка может стоять
-- в разных строках файла. Уникальность по (source, source_row) мешала бы
-- переимпорту после сдвига строк.
drop index if exists deals_source_row_uidx;
create index if not exists deals_source_row_idx
    on deals (source, source_row)
    where source is not null and source_row is not null;

-- Перенумерация существующих сделок тем же ключом, что будет считать загрузчик.
-- Ключ берётся из source_payload, то есть из файла: ручные правки
-- original_document_number на привязку не влияют.
with target as (
    select
        d.id,
        buyers_deal_number(
            d.customer_id,
            d.source_payload->>'documentType',
            d.source_payload->>'documentNumber',
            d.source_payload->>'documentDate',
            d.source_payload->>'paymentDate',
            d.source_payload->>'dealAmount'
        ) as new_number,
        row_number() over (
            partition by buyers_deal_number(
                d.customer_id,
                d.source_payload->>'documentType',
                d.source_payload->>'documentNumber',
                d.source_payload->>'documentDate',
                d.source_payload->>'paymentDate',
                d.source_payload->>'dealAmount'
            )
            order by d.id
        ) as same_key_position
    from deals d
    where d.source = 'buyers'
)
update deals d
set deal_number = case
        when t.same_key_position = 1 then t.new_number
        -- Дубль в файле: ключ занят более ранней сделкой. Такая строка
        -- остаётся в базе, но переимпорт её больше не тронет.
        else t.new_number || '-dubl-' || d.id
    end,
    updated_at = now()
from target t
where d.id = t.id
  and d.deal_number is distinct from case
        when t.same_key_position = 1 then t.new_number
        else t.new_number || '-dubl-' || d.id
    end;
