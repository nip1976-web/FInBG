-- Леналессервис, платёж #1194 на 895,00 EUR — вторая половина счёта 251,
-- а не счёта 245. Решение Николая 14.09.2026.
--
-- В назначении стоит «ОПЛ. 50% ПО СЧЕТУ № 245», но половина счёта 245 — это
-- 2 097,50 EUR, а не 895. Счёт 251 выставлен на 1 790 EUR, то есть ровно две
-- половины по 895, и одна из них уже оплачена платежом #1049 («предоплата по
-- сч № 251»). После перевода сходятся оба счёта до цента: 245 закрывают два
-- платежа по 2 097,50, а 251 — два по 895.

insert into payment_document_overrides (payment_id, document_kind, document_number)
values (1194, 'invoice', '251')
on conflict (payment_id) do update
    set document_kind   = excluded.document_kind,
        document_number = excluded.document_number,
        updated_at      = now();

delete from payment_manager_assignments where payment_id = 1194;
