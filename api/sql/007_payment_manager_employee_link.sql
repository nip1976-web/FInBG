begin;

-- Link Bitrix-sourced payment manager assignments to the employees table so
-- the same person shows up with one canonical spelling everywhere, instead
-- of a free-text name that can drift from how the deal-linked manager is
-- spelled (employees.full_name via deals.manager_id).
alter table payment_manager_assignments
    add column if not exists bitrix_user_id bigint,
    add column if not exists manager_id bigint references employees(id);

create index if not exists payment_manager_assignments_manager_id_idx
    on payment_manager_assignments(manager_id);

commit;
