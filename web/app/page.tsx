"use client";

import { useEffect, useMemo, useState } from "react";

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

const periods = {
  month: {
    label: "Июль 2026",
    revenue: "8 420 000 ₽",
    profit: "1 184 000 ₽",
    margin: "14,1%",
    cash: "3 276 400 ₽",
    inflow: "8,42 млн",
    outflow: "7,24 млн",
  },
  quarter: {
    label: "2 квартал 2026",
    revenue: "23 870 000 ₽",
    profit: "3 116 000 ₽",
    margin: "13,1%",
    cash: "3 276 400 ₽",
    inflow: "23,87 млн",
    outflow: "20,75 млн",
  },
  year: {
    label: "2026 год",
    revenue: "49 310 000 ₽",
    profit: "6 842 000 ₽",
    margin: "13,9%",
    cash: "3 276 400 ₽",
    inflow: "49,31 млн",
    outflow: "42,47 млн",
  },
};

const nav = [
  "Обзор",
  "Сделки",
  "Деньги",
  "Покупатели",
  "Поставщики",
  "Расходы",
  "Склад",
  "Отчёты",
];

const cashBars = [
  { month: "Фев", income: 52, expense: 39 },
  { month: "Мар", income: 66, expense: 58 },
  { month: "Апр", income: 48, expense: 54 },
  { month: "Май", income: 74, expense: 61 },
  { month: "Июн", income: 62, expense: 49 },
  { month: "Июл", income: 86, expense: 72 },
];

export default function Home() {
  const [period, setPeriod] = useState<keyof typeof periods>("month");
  const [active, setActive] = useState("Обзор");
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [apiConnected, setApiConnected] = useState(false);
  const data = useMemo(() => periods[period], [period]);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${API_URL}/api/dashboard/summary`, {
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
  }, []);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">ФК</span>
          <div>
            <strong>ФинКонтроль</strong>
            <span>управленческий учёт</span>
          </div>
        </div>

        <nav aria-label="Основная навигация">
          {nav.map((item) => (
            <button
              className={active === item ? "nav-item active" : "nav-item"}
              key={item}
              onClick={() => setActive(item)}
              type="button"
            >
              <span className="nav-dot" aria-hidden="true" />
              {item}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <span className="avatar">Н</span>
          <div>
            <strong>Николай</strong>
            <span>Администратор</span>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Финансовая картина</p>
            <h1>{active}</h1>
          </div>
          <div className="topbar-actions">
            <span className={apiConnected ? "connection-badge connected" : "connection-badge"}>
              {apiConnected ? "База VPS подключена" : "Подключение к VPS…"}
            </span>
            <select
              aria-label="Период отчёта"
              value={period}
              onChange={(event) =>
                setPeriod(event.target.value as keyof typeof periods)
              }
            >
              <option value="month">Месяц</option>
              <option value="quarter">Квартал</option>
              <option value="year">Год</option>
            </select>
            <button className="primary-button" type="button">
              + Новая операция
            </button>
          </div>
        </header>

        <div className="context-line">
          <span>{data.label}</span>
          <span>Обновлено сегодня, 19:42</span>
        </div>

        <section className="metrics" aria-label="Ключевые показатели">
          <article className="metric-card featured">
            <div className="metric-heading">
              <span>Деньги сейчас</span>
              <span className="trend positive">+6,8%</span>
            </div>
            <strong>{formatRub(summary?.cash_balance_rub)}</strong>
            <p>Хольц, ИП и наличные</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>Выручка</span>
              <span className="trend positive">+12,4%</span>
            </div>
            <strong>{formatRub(summary?.revenue_rub)}</strong>
            <p>по данным PostgreSQL</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>Прибыль</span>
              <span className="trend positive">+9,1%</span>
            </div>
            <strong>—</strong>
            <p>рассчитаем после импорта сделок</p>
          </article>
          <article className="metric-card">
            <div className="metric-heading">
              <span>К получению</span>
              <span className="trend warning">3 просрочено</span>
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
