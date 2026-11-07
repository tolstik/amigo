import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import type { AuthSession } from "../api/types";
import type { OverviewContext } from "../App";
import { formatDate, formatDateTime, formatKg, relativeTime } from "../lib/format";
import { Icon, type IconName } from "./Icon";
import { ThemeSwitcher } from "./ThemeSwitcher";

interface NavItem { to: string; label: string; icon: IconName; end?: boolean }

export const navGroups: Array<{ title: string | null; items: NavItem[] }> = [
  { title: null, items: [{ to: "/", label: "Обзор", icon: "overview", end: true }] },
  {
    title: "Вес и тело",
    items: [
      { to: "/progress", label: "Прогресс", icon: "progress" },
      { to: "/composition", label: "Состав тела", icon: "composition" },
      { to: "/circumference", label: "Обхваты", icon: "ruler" },
    ],
  },
  {
    title: "Активность и сердце",
    items: [
      { to: "/pressure", label: "Давление", icon: "pressure" },
      { to: "/activity", label: "Активность", icon: "steps" },
      { to: "/swimming", label: "Бассейн", icon: "waves" },
      { to: "/recovery", label: "Восстановление", icon: "moon" },
    ],
  },
  {
    title: "Медицина",
    items: [
      { to: "/labs", label: "Анализы", icon: "flask" },
      { to: "/studies", label: "Исследования", icon: "scan" },
      { to: "/assistant", label: "Ассистент", icon: "sparkle" },
      { to: "/reports/doctor", label: "Для врача", icon: "stethoscope" },
    ],
  },
  { title: "Личное", items: [{ to: "/journal", label: "Дневник", icon: "journal" }] },
];

const tabItems: NavItem[] = [
  { to: "/", label: "Обзор", icon: "overview", end: true },
  { to: "/progress", label: "Прогресс", icon: "progress" },
  { to: "/journal", label: "Дневник", icon: "journal" },
  { to: "/labs", label: "Анализы", icon: "flask" },
];

const statusLabels = {
  ok: "Данные актуальны",
  syncing: "Идёт синхронизация",
  delayed: "Синхронизация задержана",
  error: "Ошибка синхронизации",
  unknown: "Статус уточняется",
} as const;

function isActive(pathname: string, item: NavItem): boolean {
  return item.end ? pathname === item.to : pathname === item.to || pathname.startsWith(`${item.to}/`);
}

function GroupedNav({ className, onNavigate }: { className: string; onNavigate?: () => void }) {
  return (
    <nav className={className} aria-label="Разделы">
      {navGroups.map((group) => (
        <div className="nav-group" key={group.title ?? "home"}>
          {group.title && <span className="nav-group__title">{group.title}</span>}
          {group.items.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} onClick={onNavigate}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function AppLayout({ overview, session }: { overview: OverviewContext; session: AuthSession; onLogout: () => void }) {
  const location = useLocation();
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const sync = overview.data?.sync;
  const status = overview.error ? "error" : (sync?.status ?? "unknown");
  const lastSuccess = sync?.lastSuccessAt;
  const inTabs = tabItems.some((item) => isActive(location.pathname, item));

  useEffect(() => { setSheetOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!sheetOpen) return;
    sheetRef.current?.querySelector<HTMLElement>("a, button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSheetOpen(false);
        moreButton.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sheetOpen]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Перейти к содержанию</a>
      <header className="app-bar">
        <div className="app-bar__inner">
          <NavLink className="brand" to="/" aria-label="Amigo, на главную">
            <span className="brand__mark" aria-hidden="true">
              <svg viewBox="0 0 40 40"><path d="M8 27.5C10.7 19.2 14.5 12 20 7c5.5 5 9.3 12.2 12 20.5-3.9 3.6-7.9 5.5-12 5.5S11.9 31.1 8 27.5Z"/><path d="M13.5 25.5c2.3 1.7 4.4 2.5 6.5 2.5s4.2-.8 6.5-2.5"/></svg>
            </span>
            <span><strong>Amigo</strong><small>Дневник динамики</small></span>
          </NavLink>

          <div className="app-bar__tools">
            <ThemeSwitcher />
            <div className="sync-box">
              <NavLink
                className="sync-box__link"
                to="/data-quality"
                title={lastSuccess ? `Последняя синхронизация: ${formatDateTime(lastSuccess)}. Открыть качество данных` : "Открыть качество данных"}
                aria-label={`${statusLabels[status]}. Качество данных`}
              >
                <span className={`sync-dot sync-dot--${status}`} />
                <span className="sync-box__text">
                  <strong>{statusLabels[status]}</strong>
                  <small>{lastSuccess ? relativeTime(lastSuccess) : "ожидаем данные"}</small>
                </span>
              </NavLink>
              <button
                type="button"
                className={`icon-button${overview.refreshing ? " is-spinning" : ""}`}
                onClick={overview.reload}
                aria-label="Обновить данные"
                disabled={overview.refreshing}
              >
                <Icon name="refresh" />
              </button>
            </div>
            <NavLink className="profile-link" to="/profile" title={`Профиль ${session.username}`} aria-label={`Профиль ${session.username}`}>{session.username.slice(0, 1).toUpperCase()}</NavLink>
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Основная навигация">
          <GroupedNav className="main-nav" />
          <div className="sidebar-note">
            <span>Старт программы</span>
            <strong>{formatDate(overview.data?.plan.startDate)}</strong>
            <small>Цель — {formatKg(overview.data?.plan.targetWeightKg)}</small>
          </div>
        </aside>

        <main id="main-content" className="content" tabIndex={-1}>
          <Outlet context={overview} />
          <footer className="footer">
            <p>Личный аналитический дневник. Показатели давления приведены только для наблюдения и не являются медицинской оценкой.</p>
            <span>Europe/Moscow</span>
          </footer>
        </main>
      </div>

      <nav className="tabbar" aria-label="Быстрая навигация">
        {tabItems.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end}>
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </NavLink>
        ))}
        <button
          ref={moreButton}
          type="button"
          className={!inTabs ? "active" : undefined}
          aria-expanded={sheetOpen}
          aria-controls="nav-sheet"
          onClick={() => setSheetOpen((open) => !open)}
        >
          <Icon name="more" />
          <span>Ещё</span>
        </button>
      </nav>
      {sheetOpen && (
        <div className="nav-sheet-backdrop" onClick={() => setSheetOpen(false)}>
          <div
            id="nav-sheet"
            ref={sheetRef}
            className="nav-sheet"
            role="dialog"
            aria-modal="true"
            aria-label="Все разделы"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="nav-sheet__head">
              <strong>Все разделы</strong>
              <button type="button" className="icon-button" aria-label="Закрыть" onClick={() => setSheetOpen(false)}><Icon name="close" /></button>
            </div>
            <GroupedNav className="sheet-nav" onNavigate={() => setSheetOpen(false)} />
            <NavLink className="sheet-nav__extra" to="/data-quality"><Icon name="shield" /><span>Качество данных</span></NavLink>
          </div>
        </div>
      )}
    </div>
  );
}
