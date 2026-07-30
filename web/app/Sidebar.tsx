const links = [
  { label: "Обзор", href: "/" },
  { label: "Загруженные данные", href: "/data" },
  { label: "Черновик импорта", href: "/imports" },
];

const planned = [
  "Сделки",
  "Деньги",
  "Покупатели",
  "Поставщики",
  "Расходы",
  "Склад",
  "Отчёты",
];

export default function Sidebar({ current }: { current: string }) {
  const brand = (
    <>
      <span className="brand-mark">ФК</span>
      <div>
        <strong>ФинКонтроль</strong>
        <span>управленческий учёт</span>
      </div>
    </>
  );

  return (
    <aside className="sidebar">
      {current === "/" ? (
        <div className="brand">{brand}</div>
      ) : (
        <a className="brand brand-link" href="/">
          {brand}
        </a>
      )}

      <nav aria-label="Основная навигация">
        {links.map((link) => (
          <a
            className={current === link.href ? "nav-item active" : "nav-item"}
            href={link.href}
            key={link.href}
          >
            <span className="nav-dot" aria-hidden="true" />
            {link.label}
          </a>
        ))}
        {planned.map((label) => (
          <span className="nav-item nav-item-planned" key={label}>
            <span className="nav-dot" aria-hidden="true" />
            {label}
            <em>скоро</em>
          </span>
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
  );
}
