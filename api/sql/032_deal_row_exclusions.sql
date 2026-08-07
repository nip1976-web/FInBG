-- Ручные исключения строк файла сделок.
--
-- Первый случай — двойной счёт по Интернет Решения. В файле строка 2497 на
-- 17 924,46 ₽ имеет пометку самого Николая «записаны в строку 2501», а строка
-- 2501 записана на 162 199,15 ₽ — сумму, которая уже включает эти 17 924,46.
-- Товар посчитан дважды, выручка по клиенту завышена.
--
-- Деньги при этом пришли двумя платежами: 17 924,46 ₽ четвёртого марта и
-- 144 274,69 ₽ одиннадцатого, вместе ровно 162 199,15 ₽ — сумма строки 2501.
-- Поэтому лишнюю строку убираем, а её платёж переносим на настоящую сделку:
-- тогда она закрывается ровно, и повисший платёж 144 274,69 ₽ тоже находит
-- своё место.
--
-- Почему таблица, а не просто удаление сделки. Загрузчик читает файл целиком и
-- заводит сделку заново при каждой перезаливке — удалённая вернулась бы уже
-- через минуту. Решение человека должно жить отдельно от файла.
--
-- Строка опознаётся листом и номером. Это единственное место, где номер строки
-- ещё что-то значит для сделок: сама сделка опознаётся по документу.

create table if not exists deal_row_exclusions (
    id           bigint generated always as identity primary key,
    source_sheet text not null,
    source_row   integer not null,
    reason       text not null,
    created_at   timestamptz not null default now()
);

create unique index if not exists deal_row_exclusions_uidx
    on deal_row_exclusions (source_sheet, source_row);

comment on table deal_row_exclusions is
    'Строки файла Buyers, исключённые вручную. Переживают перезаливку.';

begin;

insert into deal_row_exclusions (source_sheet, source_row, reason)
values (
    'СчетаРуб',
    2497,
    'Двойной счёт: товар учтён ещё раз в строке 2501. Решение Николая 07.08.2026'
)
on conflict (source_sheet, source_row) do nothing;

-- платёж переезжает на настоящую сделку
update payment_allocations a
set deal_id = 82
where a.deal_id = 78
  and not exists (
      select 1 from payment_allocations other
      where other.deal_id = 82 and other.payment_id = a.payment_id
  );

delete from deals
where source_row = 2497
  and source_sheet = 'СчетаРуб'
  and not exists (select 1 from payment_allocations a where a.deal_id = deals.id);

-- Второй платёж, 144 274,69 ₽ от 11.03.2026, висел непривязанным именно из-за
-- двойного счёта: сумма сделки не сходилась ни с одним набором платежей.
-- Теперь остаток сделки равен ему копейка в копейку — привязываем.
insert into payment_allocations (
    payment_id, deal_id, allocated_amount_rub, source, match_confidence
)
select p.id, 82, p.amount_rub, 'manual', 'manual'
from payments p
where p.raw_counterparty = 'Интернет Решения ООО'
  and p.payment_date = date '2026-03-11'
  and p.amount_rub = 144274.69
  and not exists (select 1 from payment_allocations a where a.payment_id = p.id)
on conflict (payment_id, deal_id) do nothing;

-- «оплачено» считается по привязкам, но пересчитывается только при загрузке;
-- перенос платежа загрузкой не является
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
    from payment_allocations where deal_id = 82
) link
where d.id = 82;

commit;
