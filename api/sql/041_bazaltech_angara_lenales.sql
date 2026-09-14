-- Базалтех, Ангара Лес, Леналессервис. Решения Николая 14.09.2026.
--
-- 1. Базалтех, счёт 2827 (3 600 ₽, «возмещение расходов специалиста»): пришло
--    3 752,40 ₽. Согласовали больше, чем выставили — переплата принята.
--
-- 2. Ангара Лес, счёт 3163 (277 200 ₽, пильное кольцо): пришло 283 838 ₽,
--    та же история.
--
-- 3. Леналессервис, платёж #1045 на 206 289,96 ₽ (2 097,50 EUR по курсу ЦБ на
--    30.10.2023) зачтён на счёт 245, а не на 225. Счёт 225 закрывается первым
--    платежом ровно: 567 350 ₽ в рубль.

insert into bitrix_accepted_overpayments
    (bitrix_invoice_id, accepted_amount, currency, reason)
values
    (2827, 152.40, 'RUB',
     'Базалтех, счёт 3 600 ₽ за возмещение расходов специалиста: согласовали больше выставленного. Николай, 14.09.2026'),
    (3163, 6638.00, 'RUB',
     'Ангара Лес, счёт 277 200 ₽ за пильное кольцо: согласовали больше выставленного. Николай, 14.09.2026')
on conflict (bitrix_invoice_id) do update
    set accepted_amount = excluded.accepted_amount,
        currency        = excluded.currency,
        reason          = excluded.reason;

insert into payment_document_overrides (payment_id, document_kind, document_number)
values (1045, 'invoice', '245')
on conflict (payment_id) do update
    set document_kind   = excluded.document_kind,
        document_number = excluded.document_number,
        updated_at      = now();

delete from payment_manager_assignments where payment_id = 1045;
