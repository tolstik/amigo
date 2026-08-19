import { Icon } from "./Icon";

export function LoadingState({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`loading-state${compact ? " loading-state--compact" : ""}`} aria-live="polite" aria-busy="true">
      <span className="spinner" />
      <span>Собираем показатели…</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <div className="state-card state-card--error" role="alert">
      <div>
        <strong>Данные пока не загрузились</strong>
        <p>{message ?? "Проверьте соединение и попробуйте ещё раз."}</p>
      </div>
      <button className="button button--secondary" type="button" onClick={onRetry}>
        <Icon name="refresh" /> Повторить
      </button>
    </div>
  );
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <span className="empty-state__mark"><Icon name="activity" /></span>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}
