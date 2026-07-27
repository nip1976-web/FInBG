"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Dataset = "payments" | "deals" | "aliases";
type DealFilter = "all" | "open" | "closed" | "unmatched";
type DealColumnFilters = {
  customer: string;
  manager: string;
  documentType: string;
  documentNumber: string;
  openedOn: string;
  plannedRevenue: string;
  paidAmount: string;
  balanceRub: string;
  balanceEur: string;
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
  manager_name: string | null;
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

type DealTotals = {
  planned_revenue_rub: string;
  paid_amount_rub: string;
  balance_rub: string;
};

type PageResponse<T> = {
  page: number;
  page_size: number;
  total: number;
  totals?: DealTotals;
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

const eur = (value: number) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(value);

const date = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("ru-RU").format(new Date(`${value}T00:00:00`))
    : "—";

export default function LoadedDataPage() {
  const [dataset, setDataset] = useState<Dataset>("payments");
  const [payments, setPayments] = useState<PageResponse<Payment> | null>(null);
  const [deals, setDeals] = useState<PageResponse<Deal> | null>(null);
  const [aliases, setAliases] = useState<PageResponse<ClientAlias> | null>(null);
  const [eurRate, setEurRate] = useState<{ rate: string; rate_date: string } | null>(null);
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const [dealPayments, setDealPayments] = useState<Record<number, DealPayment[]>>({});
  const [dealPaymentsLoading, setDealPaymentsLoading] = useState<number | null>(null);
  const [candidatePayments, setCandidatePayments] = useState<AvailablePayment[]>([]);
  const [candidatePaymentsLoading, setCandidatePaymentsLoading] = useState(false);
  const [allocationAmounts, setAllocationAmounts] = useState<Record<number, string>>({});
  const [manualSavingPaymentId, setManualSavingPaymentId] = useState<number | null>(null);
  const [manualError, setManualError] = useState("");
  const [dealFilter, setDealFilter] = useState<DealFilter>("all");
  const [dealColumnFilters, setDealColumnFilters] = useState<DealColumnFilters>({
    customer: "",
    manager: "",
    documentType: "",
    documentNumber: "",
    openedOn: "",
    plannedRevenue: "",
    paidAmount: "",
    balanceRub: "",
    balanceEur: "",
  });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadAbortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
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
      if (dataset === "deals") {
        const dealParams = {
          customer: dealColumnFilters.customer,
          manager: dealColumnFilters.manager,
          document_type: dealColumnFilters.documentType,
          document_number: dealColumnFilters.documentNumber,
          opened_on: dealColumnFilters.openedOn,
          planned_revenue_rub: dealColumnFilters.plannedRevenue,
          paid_amount_rub: dealColumnFilters.paidAmount,
          balance_rub: dealColumnFilters.balanceRub,
        };
        Object.entries(dealParams).forEach(([key, value]) => {
          if (value.trim()) params.set(key, value.trim());
        });
        const balanceEur = Number(dealColumnFilters.balanceEur.replace(",", "."));
        if (dealColumnFilters.balanceEur.trim() && Number.isFinite(balanceEur) && eurRate) {
          params.set("balance_eur", String(balanceEur));
          params.set("eur_rate", eurRate.rate);
        }
      }
      const rowsResponse = await fetch(
        `${API_URL}${endpoint}?${params.toString()}`,
        { signal: controller.signal }
      );
      if (!rowsResponse.ok) {
        throw new Error("Не удалось получить загруженные данные");
      }
      const payload = await rowsResponse.json();
      if (loadAbortRef.current !== controller) return; // superseded by a newer request
      if (dataset === "payments") setPayments(payload);
      else if (dataset === "deals") setDeals(payload);
      else setAliases(payload);
    } catch (requestError) {
      if ((requestError as Error).name === "AbortError") return;
      setError((requestError as Error).message);
    } finally {
      if (loadAbortRef.current === controller) setLoading(false);
    }
  }, [dataset, dealFilter, dealColumnFilters, eurRate, page, search]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      load();
    }, 300);
    return () => clearTimeout(timeout);
  }, [load]);

  useEffect(() => {
    fetch(`${API_URL}/api/reference/eur-rate`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => setEurRate(payload))
      .catch(() => setEurRate(null));
  }, []);

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
    setDealColumnFilters({
      customer: "",
      manager: "",
      documentType: "",
      documentNumber: "",
      openedOn: "",
      plannedRevenue: "",
      paidAmount: "",
      balanceRub: "",
      balanceEur: "",
    });
    setSelectedDealId(null);
  };

  const updateDealColumnFilter = (
    key: keyof DealColumnFilters,
    value: string
  ) => {
    setDealColumnFilters((current) => ({ ...current, [key]: value }));
    setPage(1);
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

  const selectDeal = async (dealId: number, row: HTMLTableRowElement) => {
    if (selectedDealId === dealId) {
      setSelectedDealId(null);
      return;
    }
    setSelectedDealId(dealId);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const tableWrap = row.closest<HTMLDivElement>(".loaded-table-wrap");
        const tableHeader = tableWrap?.querySelector<HTMLTableSectionElement>("thead");
        if (!tableWrap || !tableHeader) return;

        const targetTop =
          tableWrap.scrollTop +
          row.getBoundingClientRect().top -
          tableWrap.getBoundingClientRect().top -
          tableHeader.getBoundingClientRect().height;

        tableWrap.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
      });
    });
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
          <div className="dataset-header">
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
            {dataset === "deals" && (
              <div className="deals-header-tools">
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
                <span className="dataset-count">
                  Найдено: <strong>{current?.total ?? 0}</strong>
                </span>
              </div>
            )}
          </div>

          {dataset !== "deals" && (
            <div className="loaded-toolbar">
              <label>
                <span>Поиск</span>
                <input
                  type="search"
                  value={search}
                  placeholder={dataset === "payments"
                    ? "Контрагент или описание…"
                    : "Название плательщика или клиента…"}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                />
              </label>
              <span>{dataset === "aliases" ? "Предложено" : "Найдено"}: <strong>{current?.total ?? 0}</strong></span>
            </div>
          )}

          {error && <div className="review-error">{error}</div>}

          <div className="loaded-table-wrap">
            {dataset === "payments" ? (
              <table className="loaded-table payments-table">
                <thead>
                  <tr>
                    <th>Дата</th>
                    <th>Контрагент и описание</th>
                    <th>Менеджер</th>
                    <th>Счёт / источник</th>
                    <th>Сумма</th>
                    <th>Тип</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan={6} className="review-empty">Загрузка…</td></tr>}
                  {!loading && payments?.items.map((item) => (
                    <tr key={item.id}>
                      <td><strong>{date(item.payment_date)}</strong></td>
                      <td className="loaded-description">
                        <strong>{item.raw_counterparty || "Не указан"}</strong>
                        <small>{item.description || "Без пояснения"}</small>
                      </td>
                      <td>{item.manager_name || "—"}</td>
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
                    <th>Менеджер</th>
                    <th>Документ</th>
                    <th>Номер</th>
                    <th>Дата документа</th>
                    <th>Сумма сделки</th>
                    <th>Оплачено клиентом</th>
                    <th>Остаток</th>
                    <th>Остаток, EUR</th>
                  </tr>
                  <tr className="deals-filter-row">
                    <th>
                      <input
                        aria-label="Поиск по покупателю"
                        type="search"
                        value={dealColumnFilters.customer}
                        placeholder="Покупатель"
                        onChange={(event) => updateDealColumnFilter("customer", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по менеджеру"
                        type="search"
                        value={dealColumnFilters.manager}
                        placeholder="Менеджер"
                        onChange={(event) => updateDealColumnFilter("manager", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по типу документа"
                        type="search"
                        value={dealColumnFilters.documentType}
                        placeholder="Тип"
                        onChange={(event) => updateDealColumnFilter("documentType", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по номеру документа"
                        type="search"
                        value={dealColumnFilters.documentNumber}
                        placeholder="Номер"
                        onChange={(event) => updateDealColumnFilter("documentNumber", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по дате документа"
                        type="search"
                        value={dealColumnFilters.openedOn}
                        placeholder="ГГГГ-ММ-ДД"
                        onChange={(event) => updateDealColumnFilter("openedOn", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по сумме сделки"
                        inputMode="decimal"
                        value={dealColumnFilters.plannedRevenue}
                        placeholder="Сумма"
                        onChange={(event) => updateDealColumnFilter("plannedRevenue", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по оплаченной сумме"
                        inputMode="decimal"
                        value={dealColumnFilters.paidAmount}
                        placeholder="Оплачено"
                        onChange={(event) => updateDealColumnFilter("paidAmount", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по остатку в рублях"
                        inputMode="decimal"
                        value={dealColumnFilters.balanceRub}
                        placeholder="Остаток"
                        onChange={(event) => updateDealColumnFilter("balanceRub", event.target.value)}
                      />
                    </th>
                    <th>
                      <input
                        aria-label="Поиск по остатку в евро"
                        inputMode="decimal"
                        value={dealColumnFilters.balanceEur}
                        placeholder="EUR"
                        onChange={(event) => updateDealColumnFilter("balanceEur", event.target.value)}
                      />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {loading && <tr><td colSpan={9} className="review-empty">Загрузка…</td></tr>}
                  {!loading && deals?.items.map((item) => (
                      <tr
                        key={item.id}
                        className={selectedDealId === item.id ? "deal-row selected" : "deal-row"}
                        tabIndex={0}
                        aria-expanded={selectedDealId === item.id}
                        onClick={(event) => selectDeal(item.id, event.currentTarget)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            selectDeal(item.id, event.currentTarget);
                          }
                        }}
                      >
                        <td className="loaded-description">
                          <strong>{item.customer_name}</strong>
                        </td>
                        <td>{item.manager_name || "—"}</td>
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
                        <td>
                          <strong>{eurRate ? eur(Number(item.balance_rub) / Number(eurRate.rate)) : "—"}</strong>
                          {eurRate && <small>ЦБ: {rub(eurRate.rate)}</small>}
                        </td>
                      </tr>
                  ))}
                </tbody>
                {!loading && deals?.totals && (
                  <tfoot>
                    <tr className="deals-total-row">
                      <td colSpan={5}>Итого по выборке</td>
                      <td />
                      <td />
                      <td>{rub(deals.totals.balance_rub)}</td>
                      <td>
                        {eurRate
                          ? eur(Number(deals.totals.balance_rub) / Number(eurRate.rate))
                          : "—"}
                      </td>
                    </tr>
                  </tfoot>
                )}
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
                      <td><span className={`data-badge ${item.status === "confirmed" ? "match-matched" : "match-review"}`}>
                        {item.status === "confirmed" ? "Подтверждено" : "На проверку"}
                      </span></td>
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
