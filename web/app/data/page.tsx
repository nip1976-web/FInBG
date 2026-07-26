"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Dataset = "payments" | "deals";
type DealFilter = "all" | "open" | "closed" | "unmatched";

type Summary = {
  payments: {
    row_count: number;
    total_amount_rub: string;
    internal_count: number;
    internal_amount_rub: string;
    date_from: string | null;
    date_to: string | null;
  };
  customer_receipts: {
    row_count: number;
    total_amount_rub: string;
    customer_count: number;
    date_from: string | null;
    date_to: string | null;
  };
  deals: {
    row_count: number;
    total_amount_rub: string;
    paid_amount_rub: string;
    open_balance_rub: string;
    closed_count: number;
    open_count: number;
    advance_count: number;
    matched_count: number;
    customer_count: number;
  };
};

type Payment = {
  id: number;
  payment_date: string;
  raw_counterparty: string | null;
  amount_rub: string;
  description: string | null;
  operation_type: string;
  source_sheet: string | null;
  source_row: number | null;
  is_internal_transfer: boolean;
  account_name: string;
};

type Deal = {
  id: number;
  deal_number: string;
  source_row: number;
  customer_name: string;
  original_document_type: string | null;
  original_document_number: string | null;
  opened_on: string;
  payment_date: string | null;
  planned_revenue_rub: string;
  paid_amount_rub: string;
  balance_rub: string;
  financial_status: "open" | "closed" | "advance";
  match_status: "matched" | "unmatched" | "review";
  manager_name: string | null;
  title: string;
  payment_id: number | null;
  linked_payment_date: string | null;
  linked_payment_amount_rub: string | null;
};

type PageResponse<T> = {
  page: number;
  page_size: number;
  total: number;
  items: T[];
};

const rub = (value: string) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value));

const shortRub = (value: string) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(Number(value));

const date = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("ru-RU").format(new Date(`${value}T00:00:00`))
    : "—";

export default function LoadedDataPage() {
  const [dataset, setDataset] = useState<Dataset>("payments");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [payments, setPayments] = useState<PageResponse<Payment> | null>(null);
  const [deals, setDeals] = useState<PageResponse<Deal> | null>(null);
  const [dealFilter, setDealFilter] = useState<DealFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      page: String(page),
      page_size: "50",
    });
    if (search.trim()) params.set("search", search.trim());

    try {
      const endpoint =
        dataset === "payments"
          ? "/api/data/payments"
          : "/api/data/deals";
      if (dataset === "deals" && dealFilter === "open") {
        params.set("financial_status", "open");
      }
      if (dataset === "deals" && dealFilter === "closed") {
        params.set("financial_status", "closed");
      }
      if (dataset === "deals" && dealFilter === "unmatched") {
        params.set("match_status", "unmatched");
      }
      const [summaryResponse, rowsResponse] = await Promise.all([
        fetch(`${API_URL}/api/data/summary`),
        fetch(`${API_URL}${endpoint}?${params.toString()}`),
      ]);
      if (!summaryResponse.ok || !rowsResponse.ok) {
        throw new Error("Не удалось получить загруженные данные");
      }
      setSummary(await summaryResponse.json());
      const payload = await rowsResponse.json();
      if (dataset === "payments") setPayments(payload);
      else setDeals(payload);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setLoading(false);
    }
  }, [dataset, dealFilter, page, search]);

  useEffect(() => {
    load();
  }, [load]);

  const current = dataset === "payments" ? payments : deals;
  const totalPages = current
    ? Math.max(1, Math.ceil(current.total / current.page_size))
    : 1;

  const chooseDataset = (value: Dataset) => {
    setDataset(value);
    setPage(1);
    setSearch("");
    setDealFilter("all");
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand brand-link" href="/">
          <span className="brand-mark">ФК</span>
          <div>
            <strong>ФинКонтроль</strong>
            <span>управленческий учёт</span>
          </div>
        </a>
        <nav aria-label="Основная навигация">
          <a className="nav-item" href="/"><span className="nav-dot" />Обзор</a>
          <a className="nav-item active" href="/data"><span className="nav-dot" />Загруженные данные</a>
          <a className="nav-item" href="/imports"><span className="nav-dot" />Черновик импорта</a>
        </nav>
        <div className="sidebar-footer">
          <span className="avatar">Н</span>
          <div><strong>Николай</strong><span>Администратор</span></div>
        </div>
      </aside>

      <section className="workspace loaded-workspace">
        <header className="topbar loaded-topbar">
          <div>
            <p className="eyebrow">Рабочая база · PostgreSQL на VPS</p>
            <h1>Загруженные данные</h1>
          </div>
          <span className="connection-badge connected">База VPS подключена</span>
        </header>

        <p className="review-intro">
          Входящие платежи сопоставляются со сделками Buyers. Неоднозначные
          строки остаются без связи для ручной проверки.
        </p>

        <section className="loaded-summary">
          <article>
            <span>Входящие Payments</span>
            <strong>{summary?.payments.row_count ?? 0}</strong>
            <b>{shortRub(summary?.payments.total_amount_rub ?? "0")}</b>
            <small>{date(summary?.payments.date_from)} — {date(summary?.payments.date_to)}</small>
          </article>
          <article>
            <span>Сделки Buyers</span>
            <strong>{summary?.deals.row_count ?? 0}</strong>
            <b>{shortRub(summary?.deals.total_amount_rub ?? "0")}</b>
            <small>{summary?.deals.customer_count ?? 0} покупателей</small>
          </article>
          <article>
            <span>Закрытые сделки</span>
            <strong>{summary?.deals.closed_count ?? 0}</strong>
            <b>{summary?.deals.matched_count ?? 0} связаны</b>
            <small>с входящими платежами</small>
          </article>
          <article>
            <span>Открытый остаток</span>
            <strong>{summary?.deals.open_count ?? 0}</strong>
            <b>{shortRub(summary?.deals.open_balance_rub ?? "0")}</b>
            <small>сделок требуют оплаты</small>
          </article>
        </section>

        <section className="loaded-panel">
          <div className="dataset-tabs" role="tablist" aria-label="Источники данных">
            <button
              className={dataset === "payments" ? "active" : ""}
              onClick={() => chooseDataset("payments")}
              role="tab"
              aria-selected={dataset === "payments"}
            >
              Входящие Payments
            </button>
            <button
              className={dataset === "deals" ? "active" : ""}
              onClick={() => chooseDataset("deals")}
              role="tab"
              aria-selected={dataset === "deals"}
            >
              Сделки Buyers
            </button>
          </div>

          <div className="loaded-toolbar">
            <label>
              <span>Поиск</span>
              <input
                type="search"
                value={search}
                placeholder={
                  dataset === "payments"
                    ? "Контрагент или описание…"
                    : "Клиент, сделка или менеджер…"
                }
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            {dataset === "deals" && (
              <label className="deal-filter">
                <span>Состояние</span>
                <select
                  value={dealFilter}
                  onChange={(event) => {
                    setDealFilter(event.target.value as DealFilter);
                    setPage(1);
                  }}
                >
                  <option value="all">Все сделки</option>
                  <option value="open">Открытые</option>
                  <option value="closed">Закрытые</option>
                  <option value="unmatched">Без связи с платежом</option>
                </select>
              </label>
            )}
            <span>Найдено: <strong>{current?.total ?? 0}</strong></span>
          </div>

          {error && <div className="review-error">{error}</div>}

          <div className="loaded-table-wrap">
            {dataset === "payments" ? (
              <table className="loaded-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Контрагент и описание</th>
                    <th>Счёт / источник</th>
                    <th>Сумма</th>
                    <th>Тип</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan={5} className="review-empty">Загрузка…</td></tr>}
                  {!loading && payments?.items.map((item) => (
                    <tr key={item.id}>
                      <td><strong>{date(item.payment_date)}</strong></td>
                      <td className="loaded-description">
                        <strong>{item.raw_counterparty || "Не указан"}</strong>
                        <small>{item.description || "Без пояснения"}</small>
                      </td>
                      <td>
                        <strong>{item.account_name}</strong>
                        <small>{item.source_sheet}, строка {item.source_row}</small>
                      </td>
                      <td className="amount-inflow"><strong>+{rub(item.amount_rub)}</strong></td>
                      <td>
                        {item.is_internal_transfer
                          ? <span className="data-badge internal">Внутренний</span>
                          : <span className="data-badge">Входящий</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="loaded-table deals-table">
                <thead>
                  <tr>
                    <th>Сделка</th>
                    <th>Покупатель и документ</th>
                    <th>Сумма / оплачено</th>
                    <th>Остаток и состояние</th>
                    <th>Связь с платежом</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan={5} className="review-empty">Загрузка…</td></tr>}
                  {!loading && deals?.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.deal_number}</strong>
                        <small>от {date(item.opened_on)} · строка {item.source_row}</small>
                      </td>
                      <td className="loaded-description">
                        <strong>{item.customer_name}</strong>
                        <small>
                          {item.original_document_type || "Документ"}{" "}
                          {item.original_document_number || "—"} · {item.title}
                        </small>
                      </td>
                      <td>
                        <strong>{rub(item.planned_revenue_rub)}</strong>
                        <small>Оплачено: {rub(item.paid_amount_rub)}</small>
                      </td>
                      <td>
                        <strong className={item.financial_status === "open" ? "negative-text" : "amount-inflow"}>
                          {rub(item.balance_rub)}
                        </strong>
                        <small>
                          {item.financial_status === "closed"
                            ? "Сделка закрыта"
                            : item.financial_status === "advance"
                              ? "Получен аванс"
                              : "Ожидается оплата"}
                        </small>
                      </td>
                      <td>
                        <span className={`data-badge match-${item.match_status}`}>
                          {item.match_status === "matched" ? "Платёж найден" : "Нужна проверка"}
                        </span>
                        <small>
                          {item.payment_id
                            ? `${date(item.linked_payment_date)} · ${rub(item.linked_payment_amount_rub || "0")}`
                            : item.manager_name || "Менеджер не указан"}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="review-pagination">
            <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>← Назад</button>
            <span>Страница {page} из {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Далее →</button>
          </div>
        </section>
      </section>
    </main>
  );
}
