-- ЦДС и Приангарский. Решения Николая 14.09.2026.
--
-- 1. ЦДС, платёж #2580 на 11 000 ₽. Менеджер приписал в выписке «Сч. 2951», и
--    счёт 2951 действительно есть — но на 11 040 ₽. Платёж переводим на него, а
--    недостающие 40 ₽ клиенту прощаем.
--
--    Отсюда знак в bitrix_accepted_overpayments: плюс — принятая переплата,
--    минус — прощённая недоплата. Таблица заводилась под переплаты, но правило
--    у них общее: человек решил, что счёт закрыт, хотя деньги со счётом не
--    сошлись. Сумма (а не флаг) по-прежнему нужна, чтобы новые деньги по этому
--    счёту вернули его на разбор.
--
-- 2. Приангарский, платёж #2538 на 28 956 EUR — второй платёж по спецификации
--    50. В Bitrix счёта на эту долю нет и не было: спецификации раньше вели без
--    счетов, и для СП50 существуют только «Авансовый платеж 15%» (счёт 2085,
--    8 689,80) и «Окончательный платеж 35%» (счёт 3087, 19 739,40). Клиент
--    сослался на единственный имевшийся у него счёт — 2085.
--
--    В Bitrix такой платёж выгружать некуда, поэтому он из выгрузки снимается,
--    и счёт 2085 закрывается своим авансом 15% ровно. На сделке (спец 50) обе
--    оплаты стоят и сходятся с файлом до копейки — деньги не теряются.

alter table bitrix_accepted_overpayments
    drop constraint bitrix_accepted_overpayments_accepted_amount_check;
alter table bitrix_accepted_overpayments
    add constraint bitrix_accepted_overpayments_accepted_amount_check
    check (accepted_amount <> 0);

comment on table bitrix_accepted_overpayments is
    'Счета, закрытые решением человека: плюс — принятая переплата, минус — прощённая недоплата';

insert into payment_document_overrides (payment_id, document_kind, document_number)
values (2580, 'invoice', '2951')
on conflict (payment_id) do update
    set document_kind   = excluded.document_kind,
        document_number = excluded.document_number,
        updated_at      = now();

delete from payment_manager_assignments where payment_id = 2580;

insert into bitrix_accepted_overpayments
    (bitrix_invoice_id, accepted_amount, currency, reason)
values
    (2951, -40.00, 'RUB',
     'ЦДС, счёт 11 040 ₽: клиент заплатил 11 000, 40 ₽ прощены. Николай, 14.09.2026')
on conflict (bitrix_invoice_id) do update
    set accepted_amount = excluded.accepted_amount,
        currency        = excluded.currency,
        reason          = excluded.reason;

insert into payment_bitrix_skips (payment_id, note)
values (2538, 'Приангарский: второй платёж по спецификации 50 (28 956 EUR). Счёта на эту долю в Bitrix нет — спецификации вели без счетов. Николай, 14.09.2026')
on conflict (payment_id) do update set note = excluded.note;

delete from payment_manager_assignments where payment_id = 2538;
