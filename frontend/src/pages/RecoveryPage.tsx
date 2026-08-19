import { useCallback, useState } from "react";
import { api, csvUrl } from "../api/client";
import type { Period } from "../api/types";
import { recoveryChartOption, sleepChartOption } from "../charts/options";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";

const metricLabels: Record<string, string> = {
  sleep_minutes: "Продолжительность сна",
  resting_heart_rate_bpm: "Пульс покоя",
  weight_kg: "Вес",
  systolic_mm_hg: "Систолическое давление",
};

function duration(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const hours = Math.floor(value / 60);
  const minutes = Math.round(value % 60);
  return `${hours} ч ${minutes} мин`;
}

function baselineHint(value: number | null | undefined, baseline: number | null | undefined, unit: string): string {
  if (value == null || baseline == null) return "Личная база появится после 28 дней";
  const delta = value - baseline;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(delta), 0)} ${unit} к личной базе`;
}

export function RecoveryPage() {
  const [period, setPeriod] = useState<Period>("90d");
  const load = useCallback((signal: AbortSignal) => api.recovery(period, signal), [period]);
  const series = useApi(load);
  const points = series.data?.points ?? [];
  const summary = series.data?.summary;

  return (
    <>
      <PageHeader
        eyebrow="Сон и восстановление"
        title="Восстановление"
        description="Продолжительность и стадии сна, пульс покоя, HRV и другие показатели, которые Mi Fitness фактически передаёт в Health Connect."
        actions={<a className="button button--secondary" href={csvUrl("recovery", period)} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели восстановления">
        <KpiCard label="Последний сон" value={duration(summary?.sleepMinutes)} hint={summary?.latestDate ? formatDate(summary.latestDate) : "Нет данных"} icon="clock" tone="violet" featured />
        <KpiCard label="Пульс покоя" value={summary?.restingHeartRateBpm == null ? "—" : `${formatNumber(summary.restingHeartRateBpm, 0)} уд/мин`} hint={baselineHint(summary?.restingHeartRateBpm, summary?.baselineRestingHeartRateBpm, "уд/мин")} icon="heart" tone="coral" />
        <KpiCard label="HRV · RMSSD" value={summary?.hrvRmssdMs == null ? "—" : `${formatNumber(summary.hrvRmssdMs, 0)} мс`} hint={baselineHint(summary?.hrvRmssdMs, summary?.baselineHrvRmssdMs, "мс")} icon="activity" tone="green" />
        <KpiCard label="SpO₂" value={summary?.spo2Pct == null ? "—" : `${formatNumber(summary.spo2Pct)}%`} hint={summary?.dataAsOf ? `Данные на ${formatDateTime(summary.dataAsOf)}` : "Показывается только при наличии"} icon="progress" tone="blue" />
      </section>

      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : points.length ? (
        <>
          <ChartCard title="Сон" subtitle="Общая продолжительность и доступные стадии" option={sleepChartOption(points)} ariaLabel="График продолжительности и стадий сна" height={390} />
          {(points.some((point) => point.restingHeartRateBpm !== null) || points.some((point) => point.hrvRmssdMs !== null)) && (
            <ChartCard title="Пульс покоя и HRV" subtitle="Два независимых показателя относительно собственной динамики" option={recoveryChartOption(points)} ariaLabel="График пульса покоя и вариабельности ритма" height={390} />
          )}
          <details className="panel recovery-table data-table-wrap">
            <summary>Таблица показателей восстановления</summary>
            <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Сон</th><th>Глубокий</th><th>REM</th><th>Пульс покоя</th><th>HRV</th><th>SpO₂</th></tr></thead><tbody>
              {[...points].reverse().map((point) => <tr key={point.measuredAt}><td>{formatDate(point.measuredAt)}</td><td>{duration(point.sleepMinutes)}</td><td>{duration(point.deepSleepMinutes)}</td><td>{duration(point.remSleepMinutes)}</td><td>{formatNumber(point.restingHeartRateBpm, 0)}</td><td>{formatNumber(point.hrvRmssdMs, 0)}</td><td>{point.spo2Pct == null ? "—" : `${formatNumber(point.spo2Pct)}%`}</td></tr>)}
            </tbody></table></div>
          </details>
          {series.data?.correlations.length ? <section className="panel correlation-panel" aria-labelledby="recovery-correlations">
            <div className="panel__head"><div><span className="eyebrow">От 8 полных недель</span><h2 id="recovery-correlations">Совместная динамика</h2></div></div>
            <div className="correlation-grid">
              {series.data.correlations.map((item) => <article key={`${item.metric}-${item.target}`}>
                <strong>{metricLabels[item.metric] ?? item.metric} ↔ {metricLabels[item.target] ?? item.target}</strong>
                <span>r = {formatNumber(item.coefficient, 2)}</span>
                <small>{item.fullOverlappingWeeks} полных недель</small>
              </article>)}
            </div>
            <p className="panel-note">{series.data.correlations[0].disclaimer}</p>
          </section> : null}
        </>
      ) : <EmptyState title="Данных сна и восстановления пока нет" text="Показатели появятся после импорта доступной истории Mi Fitness из Health Connect." />}

      <aside className="info-note"><Icon name="heart" /><p>Показатели предназначены для наблюдения за личной динамикой и не являются медицинской оценкой или рекомендацией.</p></aside>
    </>
  );
}
