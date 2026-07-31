-- Привязка сделки-спецификации к набору платежей по номеру спецификации.
--
-- Продолжение 014, но для спецификаций, и с одним важным отличием в том, как
-- ищется номер.
--
-- В 014 номер счёта искался в назначении как отдельное число. Со счетами это
-- работает: их номера четырёхзначные. У спецификаций номера короткие — 1, 4,
-- 19, 22, 23 — и голое число немедленно ловит мусор: «НДС (22%)» даёт 22,
-- «УПД №185 ОТ 19.05.2026» даёт 19. Проверено на живых данных: по спецификации
-- 22 наивный поиск набрал 798 328,24 ₽ вместо 343 846,05 ₽.
--
-- Поэтому здесь номер засчитывается только рядом со словом: «СПЕЦИЯ №22»,
-- «СПЕЦИФИКАЦИЯ № 23», «СПЕЦ №1», «спец-ии N1», «СП №55». Пишут по-разному,
-- общее — начало «сп».
--
-- Предохранители те же, что в 014: плательщик обязан совпадать с покупателем,
-- сумма набора обязана сойтись со сделкой до копейки, платёж, подходящий сразу
-- нескольким сделкам, не используется вовсе, берутся только оплаты за товар.
-- Ничего не удаляет и не переписывает.

begin;

create temporary table spec_candidates on commit drop as
with unlinked_specs as (
    select
        d.id,
        d.customer_id,
        d.original_document_number as document_number,
        d.planned_revenue_rub as deal_amount,
        c.name as customer_name
    from deals d
    join counterparties c on c.id = d.customer_id
    where d.source = 'buyers'
      and d.original_document_type ilike '%спец%'
      and d.original_document_number is not null
      and d.original_document_number <> ''
      and d.planned_revenue_rub > 0
      and not exists (
          select 1 from payment_allocations a where a.deal_id = d.id
      )
),
free_payments as (
    select p.id, p.amount_rub, p.raw_counterparty, p.description
    from payments p
    where p.source = 'payment_battery'
      and p.direction = 'inflow'
      and p.operation_type = 'Основная деятельность'
      and not p.is_internal_transfer
      and coalesce(p.raw_counterparty, '') <> ''
      and not exists (
          select 1 from payment_allocations a where a.payment_id = p.id
      )
)
select
    u.id as deal_id,
    u.deal_amount,
    f.id as payment_id,
    f.amount_rub
from unlinked_specs u
join free_payments f
  on f.description ~* ('сп[а-я.-]*[ ]*(№|N)?[ ]*' || u.document_number || '([^0-9]|$)')
 and (
      position(
        regexp_replace(lower(u.customer_name), '[^[:alnum:]]', '', 'g')
        in regexp_replace(lower(f.raw_counterparty), '[^[:alnum:]]', '', 'g')
      ) > 0
      or position(
        regexp_replace(lower(f.raw_counterparty), '[^[:alnum:]]', '', 'g')
        in regexp_replace(lower(u.customer_name), '[^[:alnum:]]', '', 'g')
      ) > 0
      or exists (
        select 1 from payment_client_aliases alias
        where alias.counterparty_id = u.customer_id
          and alias.status = 'confirmed'
          and lower(alias.payment_name) = lower(f.raw_counterparty)
      )
 );

delete from spec_candidates c
where exists (
    select 1 from spec_candidates other
    where other.payment_id = c.payment_id and other.deal_id <> c.deal_id
);

create temporary table spec_exact on commit drop as
select deal_id
from spec_candidates
group by deal_id, deal_amount
having sum(amount_rub) = deal_amount;

insert into payment_allocations (
    payment_id, deal_id, allocated_amount_rub, source, match_confidence
)
select c.payment_id, c.deal_id, c.amount_rub, 'automatic_specification_sum', 'automatic'
from spec_candidates c
join spec_exact e on e.deal_id = c.deal_id
on conflict (payment_id, deal_id) do nothing;

update deals
set match_status = 'matched',
    updated_at = now()
where id in (select deal_id from spec_exact);

commit;
