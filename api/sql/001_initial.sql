begin;

create table if not exists legal_entities (
    id bigint generated always as identity primary key,
    name text not null,
    short_name text not null,
    entity_type text not null check (entity_type in ('company', 'individual_entrepreneur')),
    tax_id text,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (short_name)
);

create table if not exists employees (
    id bigint generated always as identity primary key,
    full_name text not null,
    email text,
    is_manager boolean not null default false,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists counterparties (
    id bigint generated always as identity primary key,
    name text not null,
    counterparty_type text not null
        check (counterparty_type in ('customer', 'supplier', 'both')),
    tax_id text,
    default_currency char(3) not null default 'RUB',
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists financial_accounts (
    id bigint generated always as identity primary key,
    legal_entity_id bigint references legal_entities(id),
    name text not null,
    account_type text not null check (account_type in ('bank', 'cash')),
    currency char(3) not null default 'RUB',
    opening_balance numeric(18,2) not null default 0,
    opening_balance_date date,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (legal_entity_id, name)
);

create table if not exists deals (
    id bigint generated always as identity primary key,
    deal_number text not null unique,
    title text not null,
    customer_id bigint not null references counterparties(id),
    manager_id bigint references employees(id),
    legal_entity_id bigint references legal_entities(id),
    status text not null default 'draft'
        check (status in ('draft', 'active', 'shipped', 'completed', 'cancelled')),
    specification_number text,
    opened_on date not null default current_date,
    closed_on date,
    planned_revenue_rub numeric(18,2) not null default 0,
    planned_cost_rub numeric(18,2) not null default 0,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists supplier_orders (
    id bigint generated always as identity primary key,
    order_number text not null unique,
    supplier_id bigint not null references counterparties(id),
    deal_id bigint references deals(id),
    order_date date not null,
    currency char(3) not null default 'EUR',
    total_amount numeric(18,2) not null check (total_amount >= 0),
    exchange_rate numeric(18,6),
    total_amount_rub numeric(18,2) not null default 0,
    planned_payment_date date,
    status text not null default 'planned'
        check (status in ('planned', 'ordered', 'partially_paid', 'paid', 'received', 'cancelled')),
    created_at timestamptz not null default now()
);

create table if not exists invoices (
    id bigint generated always as identity primary key,
    invoice_number text not null,
    invoice_type text not null check (invoice_type in ('customer', 'supplier')),
    counterparty_id bigint not null references counterparties(id),
    deal_id bigint references deals(id),
    supplier_order_id bigint references supplier_orders(id),
    invoice_date date not null,
    due_date date,
    currency char(3) not null default 'RUB',
    amount_without_vat numeric(18,2) not null default 0,
    vat_amount numeric(18,2) not null default 0,
    total_amount numeric(18,2) not null check (total_amount >= 0),
    exchange_rate numeric(18,6),
    total_amount_rub numeric(18,2) not null check (total_amount_rub >= 0),
    status text not null default 'draft'
        check (status in ('draft', 'issued', 'partially_paid', 'paid', 'cancelled')),
    created_at timestamptz not null default now(),
    unique (invoice_type, counterparty_id, invoice_number)
);

create table if not exists payments (
    id bigint generated always as identity primary key,
    account_id bigint not null references financial_accounts(id),
    counterparty_id bigint references counterparties(id),
    payment_date date not null,
    direction text not null check (direction in ('inflow', 'outflow')),
    operation_type text not null default 'other',
    amount numeric(18,2) not null check (amount > 0),
    currency char(3) not null default 'RUB',
    exchange_rate numeric(18,6),
    amount_rub numeric(18,2) not null check (amount_rub > 0),
    vat_amount_rub numeric(18,2) not null default 0,
    description text,
    external_id text,
    source text not null default 'manual',
    status text not null default 'posted'
        check (status in ('draft', 'posted', 'cancelled')),
    created_at timestamptz not null default now(),
    unique (source, external_id)
);

create table if not exists payment_allocations (
    id bigint generated always as identity primary key,
    payment_id bigint not null references payments(id) on delete cascade,
    invoice_id bigint references invoices(id),
    deal_id bigint references deals(id),
    allocated_amount_rub numeric(18,2) not null check (allocated_amount_rub > 0),
    created_at timestamptz not null default now(),
    check (invoice_id is not null or deal_id is not null)
);

create table if not exists expense_categories (
    id bigint generated always as identity primary key,
    parent_id bigint references expense_categories(id),
    name text not null,
    is_direct boolean not null default false,
    is_active boolean not null default true,
    unique (parent_id, name)
);

create table if not exists expenses (
    id bigint generated always as identity primary key,
    payment_id bigint references payments(id),
    category_id bigint not null references expense_categories(id),
    deal_id bigint references deals(id),
    employee_id bigint references employees(id),
    expense_date date not null,
    amount_rub numeric(18,2) not null check (amount_rub > 0),
    vat_amount_rub numeric(18,2) not null default 0,
    description text,
    created_at timestamptz not null default now()
);

create table if not exists warehouses (
    id bigint generated always as identity primary key,
    name text not null unique,
    is_active boolean not null default true
);

create table if not exists products (
    id bigint generated always as identity primary key,
    sku text unique,
    name text not null,
    unit text not null default 'шт',
    is_active boolean not null default true
);

create table if not exists inventory_movements (
    id bigint generated always as identity primary key,
    warehouse_id bigint not null references warehouses(id),
    product_id bigint not null references products(id),
    deal_id bigint references deals(id),
    supplier_order_id bigint references supplier_orders(id),
    movement_date date not null,
    movement_type text not null
        check (movement_type in ('receipt', 'shipment', 'reserve', 'release', 'return', 'write_off')),
    quantity numeric(18,4) not null check (quantity > 0),
    unit_cost_rub numeric(18,2),
    created_at timestamptz not null default now()
);

create table if not exists fx_rates (
    rate_date date not null,
    currency char(3) not null,
    rate_to_rub numeric(18,6) not null check (rate_to_rub > 0),
    source text not null,
    primary key (rate_date, currency, source)
);

create table if not exists import_batches (
    id bigint generated always as identity primary key,
    source_file text not null,
    source_sheet text,
    imported_at timestamptz not null default now(),
    imported_rows integer not null default 0,
    skipped_rows integer not null default 0,
    status text not null default 'started'
        check (status in ('started', 'completed', 'failed')),
    error_message text
);

create table if not exists audit_log (
    id bigint generated always as identity primary key,
    table_name text not null,
    record_id bigint not null,
    action text not null check (action in ('insert', 'update', 'delete', 'cancel')),
    changed_by text,
    changed_at timestamptz not null default now(),
    old_data jsonb,
    new_data jsonb
);

create index if not exists payments_date_idx on payments(payment_date);
create index if not exists payments_counterparty_idx on payments(counterparty_id);
create index if not exists invoices_due_date_idx on invoices(due_date);
create index if not exists invoices_deal_idx on invoices(deal_id);
create index if not exists deals_customer_idx on deals(customer_id);
create index if not exists expenses_date_category_idx on expenses(expense_date, category_id);
create index if not exists inventory_product_date_idx on inventory_movements(product_id, movement_date);

create or replace view v_invoice_balances as
select
    i.*,
    greatest(
        i.total_amount_rub - coalesce(sum(pa.allocated_amount_rub), 0),
        0
    )::numeric(18,2) as balance_rub
from invoices i
left join payment_allocations pa on pa.invoice_id = i.id
group by i.id;

create or replace view v_account_balances as
select
    a.id,
    a.name,
    a.currency,
    a.opening_balance
        + coalesce(sum(
            case
                when p.direction = 'inflow' then p.amount_rub
                else -p.amount_rub
            end
        ) filter (where p.status = 'posted'), 0) as balance_rub
from financial_accounts a
left join payments p on p.account_id = a.id
group by a.id;

commit;
