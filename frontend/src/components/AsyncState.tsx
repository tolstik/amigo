import { Icon } from "./Icon";

export function LoadingState({ compact = false }: { compact?: boolean }) {
  // Skeleton blocks keep the page geometry stable while requests settle.
  return (
    <div className={`skeleton-state${compact ? " skeleton-state--compact" : ""}`} aria-live="polite" aria-busy="true">
      <span className="visually-hidden">Собираем показатели…</span>
      {!compact && (
        <div className="skeleton-row" aria-hidden="true">
          {[0, 1, 2, 3].map((index) => <span key={index} className="skeleton skeleton--card" />)}
        </div>
      )}
      <span className="skeleton skeleton--block" aria-hidden="true" />
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
