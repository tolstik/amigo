import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { Sparkline } from "./Sparkline";

interface KpiCardProps {
  label: string;
  value: string;
  hint?: ReactNode;
  icon: IconName;
  tone?: "green" | "blue" | "violet" | "coral" | "plain";
  featured?: boolean;
  /** Optional chronological daily values for a compact trend; gaps stay empty. */
  trend?: Array<number | null>;
  trendLabel?: string;
}

export function KpiCard({ label, value, hint, icon, tone = "plain", featured = false, trend, trendLabel }: KpiCardProps) {
  return (
    <article className={`kpi-card kpi-card--${tone}${featured ? " kpi-card--featured" : ""}`}>
      <div className="kpi-card__top">
        <span className="kpi-card__label">{label}</span>
        <span className="kpi-card__icon"><Icon name={icon} /></span>
      </div>
      <strong className="kpi-card__value">{value}</strong>
      {trend && trend.length > 1 && <Sparkline values={trend} label={trendLabel ?? `Динамика: ${label}`} className="kpi-card__trend" />}
      {hint && <div className="kpi-card__hint">{hint}</div>}
    </article>
  );
}
