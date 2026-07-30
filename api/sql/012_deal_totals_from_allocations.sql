-- Итоги сделки считаются по привязанным платежам, а не берутся из выгрузки Buyers.
-- Значения из файла остаются в deals.source_payload (paidAmount, balance).
-- financial_status намеренно не пересчитывается: это состояние счёта в Bitrix,
-- а не наша привязка платежей.

create or replace function recompute_deal_totals(target_deal_id bigint)
returns void
language sql
as $$
    update deals d
    set paid_amount_rub = t.paid,
        balance_rub = d.planned_revenue_rub - t.paid,
        updated_at = now()
    from (
        select coalesce(sum(allocated_amount_rub), 0) as paid
        from payment_allocations
        where deal_id = target_deal_id
    ) t
    where d.id = target_deal_id
      and (d.paid_amount_rub, d.balance_rub) is distinct from
          (t.paid, d.planned_revenue_rub - t.paid);
$$;

create or replace function payment_allocations_recompute_deal()
returns trigger
language plpgsql
as $$
begin
    if tg_op in ('INSERT', 'UPDATE') then
        perform recompute_deal_totals(new.deal_id);
    end if;
    if tg_op = 'DELETE' then
        perform recompute_deal_totals(old.deal_id);
    elsif tg_op = 'UPDATE' and new.deal_id is distinct from old.deal_id then
        perform recompute_deal_totals(old.deal_id);
    end if;
    return null;
end
$$;

drop trigger if exists payment_allocations_deal_totals on payment_allocations;
create trigger payment_allocations_deal_totals
after insert or update or delete on payment_allocations
for each row
execute function payment_allocations_recompute_deal();

-- Разовый пересчёт всех сделок из выгрузки Buyers.
update deals d
set paid_amount_rub = t.paid,
    balance_rub = d.planned_revenue_rub - t.paid,
    updated_at = now()
from (
    select d2.id,
           coalesce((
               select sum(pa.allocated_amount_rub)
               from payment_allocations pa
               where pa.deal_id = d2.id
           ), 0) as paid
    from deals d2
    where d2.source = 'buyers'
) t
where d.id = t.id
  and (d.paid_amount_rub, d.balance_rub) is distinct from
      (t.paid, d.planned_revenue_rub - t.paid);
