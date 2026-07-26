"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Dataset = "payments" | "deals" | "aliases";
type DealFilter = "all" | "open" | "closed" | "unmatched";

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

type DealPayment = {
  id: number;
  payment_date: string;
  amount_rub: string;
  raw_counterparty: string | null;
  description: string | null;
  source_sheet: string | null;
  source_row: number | null;
  account_name: string;
  allocated_amount_rub: string;
  match_confidence: "automatic" | "manual" | null;
};

type AvailablePayment = {
  id: number;
  payment_date: string;
  amount_rub: string;
  allocated_amount_rub: string;
  available_amount_rub: string;
  raw_counterparty: string | null;
  description: string | null;
  account_name: string;
};

type ClientAlias = {
  id: number;
  payment_name: string;
  buyer_name: string;
  status: "suggested" | "confirmed" | "rejected";
  evidence_type: string;
  evidence_count: number;
};

const rub = (value: string) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value));

const date = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("ru-RU").format(new Date(`${value}T00:00:00`))
    : "—";

export default function LoadedDataPage() {
  const [dataset, setDataset] = useState<Dataset>("payments");
  const [payments, setPayments] = useState<PageResponse<Payment> | null>(null);
  const [deals, setDeals] = useState<PageResponse<Deal> | null>(null);
  const [aliases, setAliases] = useState<PageResponse<ClientAlias> | null>(null);
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const [dealPayments, setDealPayments] = useState<Record<number, DealPayment[]>>({});
  const [dealPaymentsLoading, setDealPaymentsLoading] = useState<number | null>(null);
  const [candidatePayments, setCandidatePayments] = useState<AvailablePayment[]>([]);
  const [candidatePaymentsLoading, setCandidatePaymentsLoading] = useState(false);
  const [allocationAmounts, setAllocationAmounts] = useState<Record<number, string>>({});
  const [manualSavingPaymentId, setManualSavingPaymentId] = useState<number | null>(null);
  const [manualError, setManualError] = useState("");
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
      const endpoint = dataset === "payments"
        ? "/api/data/payments"
        : dataset === "deals"
          ? "/api/data/deals"
          : "/api/data/client-aliases";
      if (dataset === "deals" && dealFilter === "open") {
        params.set("financial_status", "open");
      }
      if (dataset === "deals" && dealFilter === "closed") {
        params.set("financial_status", "closed");
      }
      if (dataset === "deals" && dealFilter === "unmatched") {
        params.set("without_match", "true");
      }
      const rowsResponse = await fetch(
        `${API_URL}${endpoint}?${params.toString()}`
      );
      if (!rowsResponse.ok) {
        throw new Error("Не удалось получить загруженные данные");
      }
      const payload = await rowsResponse.json();
      if (dataset === "payments") setPayments(payload);
      else if (dataset === "deals") setDeals(payload);
      else setAliases(payload);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setLoading(false);
    }
  }, [dataset, dealFilter, page, search]);

  useEffect(() => {
    load();
  }, [load]);

  const current = dataset === "payments" ? payments : dataset === "deals" ? deals : aliases;
  const selectedDeal = deals?.items.find((item) => item.id === selectedDealId);
  const totalPages = current
    ? Math.max(1, Math.ceil(current.total / current.page_size))
    : 1;

  const chooseDataset = (value: Dataset) => {
    setDataset(value);
    setPage(1);
    setSearch("");
    setDealFilter("all");
    setSelectedDealId(null);
  };

  const loadCandidatePayments = async (dealId: number) => {
    setCandidatePaymentsLoading(true);
    setCandidatePayments([]);
    setAllocationAmounts({});
    setManualError("");
    try {
      const response = await fetch(`${API_URL}/api/data/deals/${dealId}/candidate-payments`);
      if (!response.ok) throw new Error("Не удалось получить операции покупателя.");
      const payload = await response.json();
      setCandidatePayments(payload.items);
      setAllocationAmounts(Object.fromEntries(payload.items.map((item: AvailablePayment) => [item.id, item.available_amount_rub])));
    } catch (requestError) {
      setManualError((requestError as Error).message);
    } finally {
      setCandidatePaymentsLoading(false);
    }
  };

  const saveManualLink = async (paymentId: number) => {
    const amount = allocationAmounts[paymentId];
    if (!selectedDealId || !amount) {
      setManualError("Укажите сумму для распределения.");
      return;
    }
    setManualSavingPaymentId(paymentId);
    setManualError("");
    try {
      const response = await fetch(`${API_URL}/api/data/deals/${selectedDealId}/payments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          payment_id: paymentId,
          allocated_amount_rub: amount.replace(",", "."),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Не удалось сохранить связь.");
      const dealResponse = await fetch(`${API_URL}/api/data/deals/${selectedDealId}/payments`);
      if (!dealResponse.ok) throw new Error("Связь сохранена, но список платежей не обновился.");
      const dealPayload = await dealResponse.json();
      setDealPayments((current) => ({ ...current, [selectedDealId]: dealPayload.items }));
      loadCandidatePayments(selectedDealId);
      load();
    } catch (requestError) {
      setManualError((requestError as Error).message);
    } finally {
      setManualSavingPaymentId(null);
    }
  };

  const selectDeal = async (dealId: number) => {
    if (selectedDealId === dealId) {
      setSelectedDealId(null);
      return;
    }
    setSelectedDealId(dealId);
    loadCandidatePayments(dealId);
    if (dealPayments[dealId]) return;

    setDealPaymentsLoading(dealId);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}/api/data/deals/${dealId}/payments`
      );
      if (!response.ok) throw new Error("Не удалось получить платежи сделки");
      const payload = await response.json();
      setDealPayments((current) => ({
        ...current,
        [dealId]: payload.items,
      }));
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setDealPaymentsLoading(null);
    }
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

      <section className={`workspace loaded-workspace${selectedDealId ? " deal-selected" : ""}`}>
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
            <button
              className={dataset === "aliases" ? "active" : ""}
              onClick={() => chooseDataset("aliases")}
              role="tab"
              aria-selected={dataset === "aliases"}
            >
              Соответствия клиентов
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
            <span>{dataset === "aliases" ? "Предложено" : "Найдено"}: <strong>{current?.total ?? 0}</strong></span>
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
            ) : dataset === "deals" ? (
              <table className="loaded-table deals-table">
                <thead>
                  <tr>
                    <th>Покупатель</th>
                    <th>Документ</th>
                    <th>Номер</th>
                    <th>Дата документа</th>
                    <th>Сумма сделки</th>
                    <th>Оплачено клиентом</th>
                    <th>Остаток</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan={7} className="review-empty">Загрузка…</td></tr>}
                  {!loading && deals?.items.map((item) => (
                      <tr
                        key={item.id}
                        className={selectedDealId === item.id ? "deal-row selected" : "deal-row"}
                        tabIndex={0}
                        aria-expanded={selectedDealId === item.id}
                        onClick={() => selectDeal(item.id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            selectDeal(item.id);
                          }
                        }}
                      >
                        <td className="loaded-description">
                          <strong>{item.customer_name}</strong>
                        </td>
                        <td>{item.original_document_type || "Документ"}</td>
                        <td>{item.original_document_number || "—"}</td>
                        <td>{date(item.opened_on)}</td>
                        <td>
                          <strong>{rub(item.planned_revenue_rub)}</strong>
                        </td>
                        <td>
                          <strong className="amount-inflow">{rub(item.paid_amount_rub)}</strong>
                        </td>
                        <td>
                          <strong className={item.financial_status === "open" ? "negative-text" : "amount-inflow"}>
                            {rub(item.balance_rub)}
                          </strong>
                          <small>
                            {selectedDealId === item.id ? "Платежи показаны ниже" : "Выбрать сделку"}
                          </small>
                        </td>
                      </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="loaded-table aliases-table">
                <thead><tr><th>Как указано в Payments</th><th>Клиент в Buyers</th><th>Основание</th><th>Статус</th></tr></thead>
                <tbody>
                  {loading && <tr><td colSpan={4} className="review-empty">Загрузка…</td></tr>}
                  {!loading && aliases?.items.map((item) => (
                    <tr key={item.id}>
                      <td><strong>{item.payment_name}</strong></td>
                      <td><strong>{item.buyer_name}</strong></td>
                      <td>Совпали дата и сумма оплаты · случаев: {item.evidence_count}</td>
                      <td><span className="data-badge match-review">На проверку</span></td>
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

          {dataset === "deals" && (
            <section className="deal-payments-panel" aria-live="polite">
              <div className="deal-payments-title">
                <div>
                  <strong>Связанные платежи</strong>
                  {selectedDeal && (
                    <span>{selectedDeal.deal_number} · {selectedDeal.customer_name} · менеджер: {selectedDeal.manager_name || "не указан"}</span>
                  )}
                </div>
                {selectedDeal && <span>{selectedDeal.title}</span>}
              </div>
              {!selectedDeal && <p>Выберите сделку в таблице выше, чтобы увидеть связанные с ней платежи.</p>}
              {selectedDeal && (
                <div className="candidate-payments">
                  <div className="candidate-payments-title">
                    <strong>Операции покупателя без связи со сделкой</strong>
                    <span>Можно распределить остаток одной операции между несколькими сделками.</span>
                  </div>
                  {candidatePaymentsLoading && <p>Загрузка операций покупателя…</p>}
                  {!candidatePaymentsLoading && candidatePayments.length === 0 && (
                    <p>Нераспределённых операций этого покупателя не найдено.</p>
                  )}
                  {candidatePayments.map((payment) => (
                    <article key={payment.id}>
                      <div>
                        <strong>{date(payment.payment_date)}</strong>
                        <small>{payment.account_name} · {payment.source_sheet}, строка {payment.source_row}</small>
                      </div>
                      <div>
                        <strong>{payment.raw_counterparty || "Контрагент не указан"}</strong>
                        <small>{payment.description || "Без пояснения"}</small>
                      </div>
                      <div>
                        <strong className="amount-inflow">Доступно {rub(payment.available_amount_rub)}</strong>
                        <small>Сумма операции: {rub(payment.amount_rub)}</small>
                      </div>
                      <label className="candidate-amount">
                        <span>В сделку, ₽</span>
                        <input
                          inputMode="decimal"
                          value={allocationAmounts[payment.id] ?? ""}
                          onChange={(event) => setAllocationAmounts((current) => ({ ...current, [payment.id]: event.target.value }))}
                        />
                      </label>
                      <button
                        type="button"
                        className="manual-save"
                        disabled={manualSavingPaymentId === payment.id}
                        onClick={() => saveManualLink(payment.id)}
                      >
                        {manualSavingPaymentId === payment.id ? "Сохранение…" : "Привязать"}
                      </button>
                    </article>
                  ))}
                </div>
              )}
              {manualError && <div className="manual-link-error">{manualError}</div>}
              {selectedDeal && dealPaymentsLoading === selectedDeal.id && <p>Загрузка платежей…</p>}
              {selectedDeal && dealPaymentsLoading !== selectedDeal.id && (dealPayments[selectedDeal.id]?.length ?? 0) === 0 && (
                <p>Связанных платежей пока нет.</p>
              )}
              {selectedDeal && dealPayments[selectedDeal.id]?.map((payment) => (
                <article key={payment.id}>
                  <div>
                    <strong>{date(payment.payment_date)}</strong>
                    <small>{payment.account_name} · {payment.source_sheet}, строка {payment.source_row}</small>
                  </div>
                  <div>
                    <strong>{payment.raw_counterparty || "Контрагент не указан"}</strong>
                    <small>{payment.description || "Без пояснения"}</small>
                  </div>
                  <div className="amount-inflow">
                    <strong>+{rub(payment.amount_rub)}</strong>
                    <small>В сделку: {rub(payment.allocated_amount_rub)}</small>
                  </div>
                </article>
              ))}
            </section>
          )}
        </section>
      </section>
    </main>
  );
}
