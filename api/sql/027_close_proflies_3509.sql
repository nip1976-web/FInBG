-- Сводит счёт 3509 Профлеса в одну сделку.
--
-- История. В файле счёт 3509 стоял двумя строками, и в базе получились две
-- сделки: 166 и 174. Вторая помечена суффиксом «-dubl-», и перезаливка её
-- намеренно обходит, поэтому сама она никуда не делась бы никогда.
--
-- 07.08.2026 Николай аннулировал в файле лишнюю строку — обнулил суммы и
-- написал почему. Сделка 166 подтянула настоящую строку и стала на 53 408 ₽,
-- ровно как счёт. Но платёж 46 262,40 ₽ остался висеть на сделке-дубле, и
-- сумма счёта в базе задвоилась: 99 670,40 вместо 53 408.
--
-- Здесь платёж переезжает на настоящую сделку, а пустой дубль удаляется.
-- Деньги при этом не меняются: 46 262,40 + 7 145,60 = 53 408 ₽, столько и
-- пришло от клиента. Счёт закрывается ровно.
--
-- Удаление сделки — по слову Николая 07.08.2026. Само по себе оно не делается
-- никогда: сделка без строки в файле остаётся в базе и разбирается вручную.

begin;

update payment_allocations a
set deal_id = 166
where a.deal_id = 174
  and not exists (
      -- на 166 этот платёж уже мог быть привязан; тогда переносить нечего,
      -- иначе нарушится «один платёж — одна связь со сделкой»
      select 1 from payment_allocations other
      where other.deal_id = 166 and other.payment_id = a.payment_id
  );

delete from payment_allocations where deal_id = 174;

delete from deals
where id = 174
  and deal_number like '%-dubl-%'
  and not exists (select 1 from payment_allocations a where a.deal_id = 174);

-- «Оплачено» считается по привязанным платежам, но пересчитывается при
-- загрузке файла, а не само по себе. Перенос платежа между сделками загрузку
-- не запускает, поэтому пересчитываем здесь — иначе сделка до следующей
-- перезаливки показывала бы 7 145,60 при фактически привязанных 53 408.
update deals d
set paid_amount_rub = link.paid,
    balance_rub = d.planned_revenue_rub - link.paid,
    financial_status = case
        when abs(d.planned_revenue_rub - link.paid) <= 0.01 then 'closed'
        when d.planned_revenue_rub - link.paid < 0 then 'advance'
        else 'open'
    end,
    updated_at = now()
from (
    select coalesce(sum(allocated_amount_rub), 0) as paid
    from payment_allocations where deal_id = 166
) link
where d.id = 166;

commit;
