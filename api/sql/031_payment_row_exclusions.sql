-- Ручные исключения строк платежей.
--
-- Бывает, что строка проходит по статье поступлений, но платежом по сути не
-- является. Первый случай — Техно-Групп, 28 500 ₽ от 05.05.2026: деньги
-- пришли, но сделки не состоялось и средства вернули. Статья у строки
-- «Финансовая деятельность», то есть отбор по статьям её пропускает.
--
-- Почему нужна таблица, а не пометка в промежуточных данных. Загрузчик при
-- каждой перезаливке переписывает статус строки заново — пометка стёрлась бы
-- при первой же загрузке, и платёж вернулся бы. Решение человека должно жить
-- отдельно от того, что приходит из файла, иначе оно не решение, а заметка на
-- полях.
--
-- Строка опознаётся листом и номером — так же, как опознаётся сам платёж.

create table if not exists payment_row_exclusions (
    id           bigint generated always as identity primary key,
    source_sheet text not null,
    source_row   integer not null,
    reason       text not null,
    created_at   timestamptz not null default now()
);

create unique index if not exists payment_row_exclusions_uidx
    on payment_row_exclusions (source_sheet, source_row);

comment on table payment_row_exclusions is
    'Строки файла платежей, исключённые вручную. Переживают перезаливку.';

insert into payment_row_exclusions (source_sheet, source_row, reason)
values (
    'РС-Поступ-Хольц',
    2868,
    'Техно-Групп: сделки не было, деньги вернули. Решение Николая 07.08.2026'
)
on conflict (source_sheet, source_row) do nothing;

-- Убираем сам платёж. Привязок к сделкам у него нет — проверено.
delete from payments p
where p.source = 'payment_battery'
  and exists (
      select 1 from payment_row_exclusions e
      where e.source_sheet = p.source_sheet and e.source_row = p.source_row
  )
  and not exists (
      select 1 from payment_allocations a where a.payment_id = p.id
  );

update staging_payment_operations s
set validation_status = 'excluded',
    updated_at = now()
where exists (
    select 1 from payment_row_exclusions e
    where e.source_sheet = s.source_sheet and e.source_row = s.source_row
);
