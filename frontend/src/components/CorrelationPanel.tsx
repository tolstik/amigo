import type { HealthCorrelation } from "../api/types";

interface CorrelationPanelProps {
  id: string;
  correlations: HealthCorrelation[];
  metricLabels: Record<string, string>;
}

const coefficientFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});

export function describeCorrelation(coefficient: number): string {
  const magnitude = Math.abs(coefficient);
  if (magnitude < 0.2) return "Линейная связь почти не выражена.";
  const strength = magnitude < 0.4 ? "Слабая" : magnitude < 0.7 ? "Умеренная" : "Сильная";
  const direction = coefficient < 0 ? "обратная" : "прямая";
  return `${strength} ${direction} линейная связь.`;
}

export function CorrelationPanel({ id, correlations, metricLabels }: CorrelationPanelProps) {
  if (!correlations.length) return null;
  return (
    <section className="panel correlation-panel" aria-labelledby={id}>
      <div className="panel__head correlation-panel__head">
        <div>
          <span className="eyebrow">От 8 полных недель</span>
          <h2 id={id}>Совместная динамика</h2>
          <p className="correlation-explanation">
            <strong>r — коэффициент линейной корреляции Пирсона от −1 до 1.</strong>{" "}
            Знак показывает направление совместных изменений, а |r| — их выраженность:
            ближе к 0 связь слабее, ближе к 1 — сильнее.
          </p>
        </div>
      </div>
      <div className="correlation-grid">
        {correlations.map((item) => (
          <article className="correlation-card" key={`${item.metric}-${item.target}`}>
            <h3>{metricLabels[item.metric] ?? item.metric} ↔ {metricLabels[item.target] ?? item.target}</h3>
            <span className="correlation-card__coefficient">r = {coefficientFormatter.format(item.coefficient)}</span>
            <p>{describeCorrelation(item.coefficient)}</p>
            <small>{item.fullOverlappingWeeks} полных недель</small>
          </article>
        ))}
      </div>
      <p className="correlation-note">
        {correlations[0].disclaimer} Словесная оценка условна и описывает только линейную связь.
      </p>
    </section>
  );
}
