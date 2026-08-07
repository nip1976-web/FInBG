"use client";

import { useEffect, useMemo, useState } from "react";

import Sidebar from "./Sidebar";

type DashboardSummary = {
  cash_balance_rub: string;
  inflow_rub: string;
  outflow_rub: string;
  net_cash_flow_rub: string;
  revenue_rub: string;
  receivables_rub: string;
  payables_rub: string;
};

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const formatRub = (value: string | undefined) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(Number(value ?? 0));

type PeriodKey = "month" | "quarter" | "year" | "all";

const MONTHS = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];

const iso = (value: Date) => value.toISOString().slice(0, 10);

// Границы периода считаются от сегодняшнего дня, а не зашиты в страницу:
// раньше кнопки переключали выдуманные числа, а живая цифра приходила одна
// и без периода — с загрузкой платежей за прошлые годы это стало заметно.
const periodRange = (key: PeriodKey, today: Date) => {
  const year = today.getFullYear();
  if (key === "all") {
    return { from: null, to: null, label: "За всё время" };
  }
  if (key === "year") {
    return {
      from: iso(new Date(Date.UTC(year, 0, 1))),
      to: iso(new Date(Date.UTC(year, 11, 31))),
      label: `${year} год`,
    };
  }
  if (key === "quarter") {
    const quarter = Math.floor(today.getMonth() / 3);
    return {
      from: iso(new Date(Date.UTC(year, quarter * 3, 1))),
      to: iso(new Date(Date.UTC(year, quarter * 3 + 3, 0))),
      label: `${quarter + 1} квартал ${year}`,
    };
  }
  const month = today.getMonth();
  return {
    from: iso(new Date(Date.UTC(year, month, 1))),
    to: iso(new Date(Date.UTC(year, month + 1, 0))),
    label: `${MONTHS[month]} ${year}`,
  };
};

const cashBars = [
  { month: "Фев", income: 52, expense: 39 },
  { month: "Мар", income: 66, expense: 58 },
  { month: "Апр", income: 48, expense: 54 },
  { month: "Май", income: 74, expense: 61 },
  { month: "Июн", income: 62, expense: 49 },
  { month: "Июл", income: 86, expense: 72 },
];

export default function Home() {
  const [period, setPeriod] = useState<PeriodKey>("year");
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [apiConnected, setApiConnected] = useState(false);
  const data = useMemo(() => periodRange(period, new Date()), [period]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (data.from) params.set("date_from", data.from);
    if (data.to) params.set("date_to", data.to);
    const query = params.toString();

    fetch(`${API_URL}/api/dashboard/summary${query ? `?${query}` : ""}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("API request failed");
        return response.json() as Promise<DashboardSummary>;
      })
      .then((payload) => {
        setSummary(payload);
        setApiConnected(true);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setApiConnected(false);
      });

    return () => controller.abort();
  }, [data.from, data.to]);

  return (
    <main className="app-shell">
      <Sidebar current="/" />

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Финансовая картина</p>
            <h1>Обзор</h1>
          </div>
          <div className="topbar-actions">
            <span className={apiConnected ? "connection-badge connected" : "connection-badge"}>
              {apiConnected ? "База VPS подключена" : "Подключение к VPS…"}
            </span>
            <select
              aria-label="Период отчёта"
              value={period}
              onChange={(event) => setPeriod(event.target.value as PeriodKey)}
            >
              <option value="month">Месяц</option>
              <option value="quarter">Квартал</option>
              <option value="year">Год</option>
              <option value="all">За всё время</option>
            </select>
            <button className="primary-button" type="button">
              + Новая операция
            </button>
          </div>
        </header>

        <div className="context-line">
          <span>{data.label}</span>
          <span>
            {data.from ? `с ${data.from} по ${data.to}` : "без ограничения по дате"}
          </span>
        </div>

        <section className="metrics" aria-label="Ключевые показатели">
          <article className="metric-card featured">
            <div className="metric-heading">
              <span>Поступило за период</span>
            </div>
            <strong>{formatRub(summary?.inflow_rub)}</strong>
            <p>Хольц, ИП и наличные</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>Поступило за всё время</span>
            </div>
            <strong>{formatRub(summary?.cash_balance_rub)}</strong>
            <p>всё, что загружено в базу</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>Прибыль</span>
            </div>
            <strong>—</strong>
            <p>считать не из чего: расходы не загружены</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>К получению</span>
            </div>
            <strong>{formatRub(summary?.receivables_rub)}</strong>
            <p>дебиторская задолженность</p>
          </article>
        </section>

        <section className="dashboard-grid">
          <article className="panel cash-panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">Денежный поток</p>
                <h2>Поступления и расходы</h2>
              </div>
              <div className="legend" aria-label="Легенда">
                <span><i className="income-swatch" />Поступления</span>
                <span><i className="expense-swatch" />Расходы</span>
              </div>
            </div>

            <div className="cash-summary">
              <div><span>Поступило</span><strong>{formatRub(summary?.inflow_rub)}</strong></div>
              <div><span>Потрачено</span><strong>{formatRub(summary?.outflow_rub)}</strong></div>
              <div><span>Чистый поток</span><strong className="positive-text">{formatRub(summary?.net_cash_flow_rub)}</strong></div>
            </div>

            <div className="bar-chart" aria-label="Движение денег по месяцам">
              {cashBars.map((bar) => (
                <div className="bar-group" key={bar.month}>
                  <div className="bars">
                    <i className="bar income" style={{ height: `${bar.income}%` }} />
                    <i className="bar expense" style={{ height: `${bar.expense}%` }} />
                  </div>
                  <span>{bar.month}</span>
                </div>
              ))}
            </div>
          </article>

          <article className="panel due-panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">Ближайшие 14 дней</p>
                <h2>Платёжный календарь</h2>
              </div>
              <button className="link-button" type="button">Все платежи</button>
            </div>
            <div className="due-total">
              <span>Ожидаемый остаток</span>
              <strong>2 418 900 ₽</strong>
            </div>
            <ul className="payment-list">
              <li>
                <span className="payment-date">26<br /><small>июл</small></span>
                <span><strong>ООО «ТехПром»</strong><small>Спецификация 184</small></span>
                <b className="positive-text">+840 000 ₽</b>
              </li>
              <li>
                <span className="payment-date">29<br /><small>июл</small></span>
                <span><strong>Transport GmbH</strong><small>Доставка, заказ 771</small></span>
                <b className="negative-text">−312 000 ₽</b>
              </li>
              <li>
                <span className="payment-date">02<br /><small>авг</small></span>
                <span><strong>Holz Parts AG</strong><small>Поставка оборудования</small></span>
                <b className="negative-text">−1 180 000 ₽</b>
              </li>
            </ul>
          </article>
        </section>

        <section className="panel deals-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Контроль сделок</p>
              <h2>Активные проекты</h2>
            </div>
            <button className="link-button" type="button">Открыть сделки</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Сделка</th>
                  <th>Клиент</th>
                  <th>Менеджер</th>
                  <th>Выручка</th>
                  <th>Прибыль</th>
                  <th>Оплата</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>СП-184</strong><small>Комплектующие линии</small></td>
                  <td>ООО «ТехПром»</td><td>Николай</td><td>2 480 000 ₽</td>
                  <td className="positive-text">418 000 ₽</td>
                  <td><span className="progress"><i style={{ width: "68%" }} /></span><small>68%</small></td>
                  <td><span className="status in-work">В работе</span></td>
                </tr>
                <tr>
                  <td><strong>СП-179</strong><small>Насосное оборудование</small></td>
                  <td>АО «РегионСнаб»</td><td>Алексей</td><td>1 760 000 ₽</td>
                  <td className="positive-text">226 000 ₽</td>
                  <td><span className="progress"><i style={{ width: "100%" }} /></span><small>100%</small></td>
                  <td><span className="status ready">Оплачено</span></td>
                </tr>
                <tr>
                  <td><strong>СП-191</strong><small>Запасные части</small></td>
                  <td>ООО «СеверМаш»</td><td>Николай</td><td>940 000 ₽</td>
                  <td className="positive-text">138 000 ₽</td>
                  <td><span className="progress"><i style={{ width: "30%" }} /></span><small>30%</small></td>
                  <td><span className="status waiting">Ожидает</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  );
}
