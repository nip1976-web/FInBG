-- Привязывает оба платежа Терскола к сделке по счёту 3737.
--
-- Почему правило не справилось. Автопривязка по номеру счёта требует, чтобы
-- номер в назначении совпал с номером сделки. Здесь совпадает только первый
-- платёж: второй ссылается на счёт 3741, а сделка в файле записана одной
-- строкой под номером 3737 на общую сумму.
--
-- Правило в таком случае отступает — и правильно делает: само по себе
-- «сумма сошлась» ещё не повод связывать платёж с чужим номером счёта.
-- Здесь связь подтвердил Николай 07.08.2026, проверив документы.
--
-- Суммы сходятся копейка в копейку:
--     3 638 675,63 + 3 668 296,20 = 7 306 971,83 — сумма сделки.

begin;

insert into payment_allocations (
    payment_id, deal_id, allocated_amount_rub, source, match_confidence
)
select p.id, 590, p.amount_rub, 'manual', 'manual'
from payments p
where p.id in (407, 411)
  and not exists (
      select 1 from payment_allocations a where a.payment_id = p.id
  )
on conflict (payment_id, deal_id) do nothing;

-- «оплачено» пересчитывается при загрузке файла, а ручная привязка загрузкой
-- не является — считаем здесь
update deals d
set paid_amount_rub = link.paid,
    balance_rub = d.planned_revenue_rub - link.paid,
    financial_status = case
        when abs(d.planned_revenue_rub - link.paid) <= 0.01 then 'closed'
        when d.planned_revenue_rub - link.paid < 0 then 'advance'
        else 'open'
    end,
    match_status = 'matched',
    updated_at = now()
from (
    select coalesce(sum(allocated_amount_rub), 0) as paid
    from payment_allocations where deal_id = 590
) link
where d.id = 590;

commit;
