-- Привязка сделки к набору платежей по номеру счёта.
--
-- Существующая автопривязка (import_buyers_deals.py) сравнивает один платёж с
-- одной сделкой: совпали дата и сумма — связала. Но клиенты часто платят по
-- счёту двумя частями («частичная оплата (50%)» и остаток), и тогда ни одна
-- часть сумме не равна — сделка остаётся без привязок, хотя деньги пришли.
--
-- Правило здесь: несколько свободных платежей одного клиента, ссылающихся в
-- назначении на один и тот же номер счёта, складываются и привязываются к этой
-- сделке. Предохранители, без которых правило опасно:
--
--   * плательщик обязан совпадать с покупателем — номер счёта в назначении сам
--     по себе ненадёжен, он мог быть указан ошибочно или принадлежать другому
--     клиенту;
--   * сумма набора обязана сойтись со сделкой точно, до копейки — «примерно
--     подходит» не привязывается никогда;
--   * платёж, который подходит сразу нескольким сделкам, не используется вовсе:
--     такой случай разбирает человек;
--   * берутся только оплаты за товар — возвраты, залоги и внутренние переводы
--     выручкой не являются и на сделке им делать нечего.
--
-- Ничего не удаляет и не переписывает: только добавляет связи там, где их нет.

begin;

create temporary table match_candidates on commit drop as
with unlinked_deals as (
    select
        d.id,
        d.customer_id,
        d.original_document_number as document_number,
        d.planned_revenue_rub as deal_amount,
        c.name as customer_name
    from deals d
    join counterparties c on c.id = d.customer_id
    where d.source = 'buyers'
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
from unlinked_deals u
join free_payments f
  -- номер счёта в назначении, но именно как отдельное число: иначе счёт 3117
  -- нашёлся бы внутри «43117» или внутри суммы платежа
  on f.description ~ ('(^|[^0-9])' || u.document_number || '([^0-9]|$)')
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

-- платёж, подходящий сразу нескольким сделкам, отбрасываем целиком:
-- к какой из них он относится, из данных не видно
delete from match_candidates c
where exists (
    select 1 from match_candidates other
    where other.payment_id = c.payment_id and other.deal_id <> c.deal_id
);

create temporary table exact_matches on commit drop as
select deal_id
from match_candidates
group by deal_id, deal_amount
having sum(amount_rub) = deal_amount;

insert into payment_allocations (
    payment_id, deal_id, allocated_amount_rub, source, match_confidence
)
select c.payment_id, c.deal_id, c.amount_rub, 'automatic_invoice_sum', 'automatic'
from match_candidates c
join exact_matches e on e.deal_id = c.deal_id
on conflict (payment_id, deal_id) do nothing;

update deals
set match_status = 'matched',
    updated_at = now()
where id in (select deal_id from exact_matches);

commit;
