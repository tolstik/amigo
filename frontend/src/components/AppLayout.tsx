import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import type { AuthSession } from "../api/types";
import type { OverviewContext } from "../App";
import { formatDate, formatDateTime, formatKg, relativeTime } from "../lib/format";
import { Icon, type IconName } from "./Icon";
import { ThemeSwitcher } from "./ThemeSwitcher";

const navItems: Array<{ to: string; label: string; icon: IconName; end?: boolean }> = [
  { to: "/", label: "Обзор", icon: "overview", end: true },
  { to: "/progress", label: "Прогресс", icon: "progress" },
  { to: "/history", label: "Вся история", icon: "history" },
  { to: "/pressure", label: "Давление", icon: "pressure" },
  { to: "/composition", label: "Состав тела", icon: "composition" },
  { to: "/activity", label: "Активность", icon: "activity" },
  { to: "/recovery", label: "Восстановление", icon: "clock" },
  { to: "/labs", label: "Анализы", icon: "composition" },
  { to: "/assistant", label: "Ассистент", icon: "sparkle" },
];

const statusLabels = {
  ok: "Данные актуальны",
  syncing: "Идёт синхронизация",
  delayed: "Синхронизация задержана",
  error: "Ошибка синхронизации",
  unknown: "Статус уточняется",
} as const;

export function AppLayout({ overview, session, onLogout }: { overview: OverviewContext; session: AuthSession; onLogout: () => void }) {
  const sync = overview.data?.sync;
  const status = overview.error ? "error" : (sync?.status ?? "unknown");
  const lastSuccess = sync?.lastSuccessAt;

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
            <NavLink className="profile-link" to="/profile" title={`Профиль ${session.username}`}>{session.username.slice(0, 1).toUpperCase()}</NavLink>
            <button className="button button--ghost logout-button" type="button" onClick={async () => { await api.logout(); onLogout(); }}>Выйти</button>
            <div className="sync-box" title={lastSuccess ? `Последняя синхронизация: ${formatDateTime(lastSuccess)}` : undefined}>
              <span className={`sync-dot sync-dot--${status}`} />
              <span>
                <strong>{statusLabels[status]}</strong>
                <small>{lastSuccess ? relativeTime(lastSuccess) : "ожидаем данные"}</small>
              </span>
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
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Основная навигация">
          <nav className="main-nav">
            {navItems.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}>
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
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
    </div>
  );
}
