"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import Sidebar from "../Sidebar";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type FilterMode = "all" | "missing" | "internal" | "duplicates";

type Overview = {
  total_rows: number;
  missing_category_count: number;
  internal_count: number;
  duplicate_count: number;
  pending_count: number;
  ready_count: number;
  excluded_count: number;
};

type ImportSummary = {
  latest_batch: {
    source_file: string;
    imported_at: string;
    imported_rows: number;
    status: string;
    period_from: string;
  } | null;
  overview: Overview;
};

type Operation = {
  id: number;
  source_sheet: string;
  source_row: number;
  direction: "inflow" | "outflow";
  counterparty: string | null;
  amount_rub: string;
  operation_date: string;
  explanation: string | null;
  category: string | null;
  account_code: string | null;
  manager: string | null;
  likely_internal_transfer: boolean;
  possible_duplicate: boolean;
  validation_status: "pending" | "ready" | "excluded" | "error" | "imported";
};

type OperationsResponse = {
  page: number;
  page_size: number;
  total: number;
  items: Operation[];
};

const formatRub = (value: string) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value));

const formatDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU").format(
    new Date(`${value}T00:00:00`)
  );

export default function ImportReviewPage() {
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [operations, setOperations] = useState<OperationsResponse | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [mode, setMode] = useState<FilterMode>("all");
  const [sheet, setSheet] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const sheets = useMemo(
    () => [
      "РС-Поступ-Хольц",
      "РС-Плат-Хольц",
      "ПоступленияН",
      "ПлатежиН",
    ],
    []
  );

  const loadSummary = useCallback(async () => {
    const [summaryResponse, categoriesResponse] = await Promise.all([
      fetch(`${API_URL}/api/imports/staging-summary`),
      fetch(`${API_URL}/api/imports/categories`),
    ]);
    if (!summaryResponse.ok || !categoriesResponse.ok) {
      throw new Error("Не удалось получить сводку импорта");
    }
    setSummary((await summaryResponse.json()) as ImportSummary);
    const categoryPayload = (await categoriesResponse.json()) as {
      items: string[];
    };
    setCategories(categoryPayload.items);
  }, []);

  const loadOperations = useCallback(async () => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: "50",
    });
    if (sheet) params.set("source_sheet", sheet);
    if (search.trim()) params.set("search", search.trim());
    if (mode === "missing") params.set("missing_category", "true");
    if (mode === "internal") params.set("only_internal", "true");
    if (mode === "duplicates") params.set("only_duplicates", "true");

    const response = await fetch(
      `${API_URL}/api/imports/operations?${params.toString()}`
    );
    if (!response.ok) throw new Error("Не удалось получить операции");
    setOperations((await response.json()) as OperationsResponse);
  }, [mode, page, search, sheet]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setLoading(true);
      setError("");
      Promise.all([loadSummary(), loadOperations()])
        .catch((requestError: Error) => setError(requestError.message))
        .finally(() => setLoading(false));
    }, 0);
    return () => clearTimeout(timeout);
  }, [loadOperations, loadSummary]);

  const updateOperation = async (
    operationId: number,
    patch: Partial<Operation>
  ) => {
    setSavingId(operationId);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}/api/imports/operations/${operationId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        }
      );
      if (!response.ok) throw new Error("Изменение не сохранено");
      const updated = (await response.json()) as Partial<Operation>;
      setOperations((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.id === operationId ? { ...item, ...updated } : item
              ),
            }
          : current
      );
      await loadSummary();
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  const selectMode = (nextMode: FilterMode) => {
    setMode(nextMode);
    setPage(1);
  };

  const overview = summary?.overview;
  const totalPages = operations
    ? Math.max(1, Math.ceil(operations.total / operations.page_size))
    : 1;

  return (
    <main className="app-shell">
      <Sidebar current="/imports" />

      <section className="workspace review-workspace">
        <header className="topbar review-topbar">
          <div>
            <p className="eyebrow">PaymentBatteryRUS · с 01.01.2026</p>
            <h1>Проверка импорта</h1>
          </div>
          <div className="review-status">
            <span className="connection-badge connected">База VPS подключена</span>
            <span>{overview?.ready_count ?? 0} готово</span>
            <span>{overview?.pending_count ?? 0} ожидает проверки</span>
          </div>
        </header>

        <p className="review-intro">
          Здесь можно проверить строки Excel перед переносом в рабочие платежи.
          Все изменения сохраняются только в staging.
        </p>

        <section className="review-metrics" aria-label="Сводка проверки">
          <button className={mode === "all" ? "review-stat selected" : "review-stat"} onClick={() => selectMode("all")}>
            <span>Всего операций</span><strong>{overview?.total_rows ?? 0}</strong>
          </button>
          <button className={mode === "missing" ? "review-stat selected" : "review-stat"} onClick={() => selectMode("missing")}>
            <span>Без статьи</span><strong>{overview?.missing_category_count ?? 0}</strong>
          </button>
          <button className={mode === "internal" ? "review-stat selected" : "review-stat"} onClick={() => selectMode("internal")}>
            <span>Внутренние переводы</span><strong>{overview?.internal_count ?? 0}</strong>
          </button>
          <button className={mode === "duplicates" ? "review-stat selected" : "review-stat"} onClick={() => selectMode("duplicates")}>
            <span>Возможные дубли</span><strong>{overview?.duplicate_count ?? 0}</strong>
          </button>
        </section>

        <section className="review-panel">
          <div className="review-filters">
            <label>
              <span>Поиск</span>
              <input
                type="search"
                placeholder="Контрагент, описание, статья…"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            <label>
              <span>Источник</span>
              <select
                value={sheet}
                onChange={(event) => {
                  setSheet(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">Все листы</option>
                {sheets.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <div className="review-count">
              Найдено: <strong>{operations?.total ?? 0}</strong>
            </div>
          </div>

          {error && <div className="review-error">{error}</div>}

          <div className="review-table-wrap">
            <table className="review-table">
              <thead>
                <tr>
                  <th>Дата / источник</th>
                  <th>Контрагент и описание</th>
                  <th>Сумма</th>
                  <th>Статья</th>
                  <th>Контроль</th>
                  <th>Решение</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td colSpan={6} className="review-empty">Загрузка операций…</td></tr>
                )}
                {!loading && operations?.items.length === 0 && (
                  <tr><td colSpan={6} className="review-empty">По выбранным условиям строк нет</td></tr>
                )}
                {!loading && operations?.items.map((operation) => (
                  <tr
                    key={operation.id}
                    className={[
                      operation.possible_duplicate ? "row-duplicate" : "",
                      operation.validation_status === "excluded" ? "row-excluded" : "",
                    ].join(" ")}
                  >
                    <td>
                      <strong>{formatDate(operation.operation_date)}</strong>
                      <small>{operation.source_sheet}, строка {operation.source_row}</small>
                    </td>
                    <td className="review-description">
                      <strong>{operation.counterparty || "Контрагент не указан"}</strong>
                      <small>{operation.explanation || "Без пояснения"}</small>
                      {operation.manager && <em>Менеджер: {operation.manager}</em>}
                    </td>
                    <td className={operation.direction === "inflow" ? "amount-inflow" : "amount-outflow"}>
                      <strong>{operation.direction === "inflow" ? "+" : "−"}{formatRub(operation.amount_rub)}</strong>
                      <small>{operation.account_code}</small>
                    </td>
                    <td>
                      <select
                        className={!operation.category ? "category-select missing" : "category-select"}
                        value={operation.category ?? ""}
                        disabled={savingId === operation.id}
                        onChange={(event) =>
                          updateOperation(operation.id, {
                            category: event.target.value || null,
                          })
                        }
                      >
                        <option value="">Не назначена</option>
                        {categories.map((category) => (
                          <option key={category} value={category}>{category}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <label className="review-check">
                        <input
                          type="checkbox"
                          checked={operation.likely_internal_transfer}
                          disabled={savingId === operation.id}
                          onChange={(event) =>
                            updateOperation(operation.id, {
                              likely_internal_transfer: event.target.checked,
                            })
                          }
                        />
                        Внутренний
                      </label>
                      {operation.possible_duplicate && (
                        <button
                          className="duplicate-flag"
                          disabled={savingId === operation.id}
                          onClick={() =>
                            updateOperation(operation.id, {
                              possible_duplicate: false,
                            })
                          }
                          type="button"
                        >
                          Возможный дубль · снять
                        </button>
                      )}
                    </td>
                    <td>
                      <select
                        className={`decision-select status-${operation.validation_status}`}
                        value={operation.validation_status}
                        disabled={savingId === operation.id}
                        onChange={(event) =>
                          updateOperation(operation.id, {
                            validation_status: event.target.value as Operation["validation_status"],
                          })
                        }
                      >
                        <option value="pending">На проверке</option>
                        <option value="ready">Готово</option>
                        <option value="excluded">Исключить</option>
                      </select>
                      {savingId === operation.id && <small>Сохранение…</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
