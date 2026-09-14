-- Принятые переплаты: счёт закрыт решением человека, хотя денег пришло больше.
--
-- Выгрузка в Bitrix держит счёт вне выгрузки, если оплачено заметно больше
-- счёта: такие деньги почти всегда чужие — зачёт в другую спецификацию,
-- возврат, второй счёт. Но бывает и третье: клиент посчитал рубли по своему
-- курсу и заплатил на несколько евро больше, а счёт при этом закрыт. Правилом
-- это не ловится (у КРОНЫ 0,02%, у Русфореста 5,3% — одно правило накрыло бы
-- либо ничего, либо лишнего), и решение принимает человек по одному счёту.
--
-- Почему таблица, а не пометка в Bitrix: заливки и обмены переписывают поля
-- счёта, а решение человека должно жить отдельно и переживать их.
--
-- Храним не флаг, а **сумму** принятой переплаты. Если завтра по этому счёту
-- придут ещё деньги, превышение станет больше принятого, и счёт вернётся на
-- разбор — а флаг молча закрыл бы и новые деньги.

create table if not exists bitrix_accepted_overpayments (
    bitrix_invoice_id bigint      primary key,
    accepted_amount   numeric(18, 2) not null check (accepted_amount > 0),
    currency          char(3)     not null,
    reason            text        not null,
    created_at        timestamptz not null default now()
);

comment on table bitrix_accepted_overpayments is
    'Счета, закрытые решением человека при переплате: сумма, которая принята';

insert into bitrix_accepted_overpayments
    (bitrix_invoice_id, accepted_amount, currency, reason)
values
    (1255, 6.29, 'EUR',
     'КРОНА, счёт 27 900 EUR: клиент посчитал по своему курсу, 0,02% сверх счёта. Николай, 14.09.2026'),
    (275, 8.44, 'EUR',
     'Рубцовский ЛДК, счёт 12 112 EUR: два платежа сошлись ровно, третий по курсу другой даты, 0,07% сверх счёта. Николай, 14.09.2026')
on conflict (bitrix_invoice_id) do update
    set accepted_amount = excluded.accepted_amount,
        currency        = excluded.currency,
        reason          = excluded.reason;
