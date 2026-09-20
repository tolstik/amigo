import { useCallback, useEffect } from "react";
import { api, csvUrl } from "../api/client";
import { recoveryChartOption, sleepChartOption } from "../charts/options";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { CorrelationPanel } from "../components/CorrelationPanel";
import { EvidenceChips } from "../components/EvidenceChips";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { WatchHeartRateChart } from "../components/WatchHeartRateChart";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
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

function heartRateRange(minimum: number | null | undefined, maximum: number | null | undefined): string {
  if (minimum == null || maximum == null) return "Среднее по доступным замерам часов";
  return `Диапазон за день: ${formatNumber(minimum, 0)}–${formatNumber(maximum, 0)} уд/мин`;
}

export function RecoveryPage() {
  const [period, setPeriod] = useChartPeriod("90d");
  const load = useCallback((signal: AbortSignal) => api.recovery(period, signal), [period]);
  const series = useApi(load);
  const loadAi = useCallback((signal: AbortSignal) => api.aiAnalysis(signal), []);
  const ai = useApi(loadAi);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") ai.reload(); };
    const timer = window.setInterval(refresh, ai.data?.status === "pending" ? 30_000 : 300_000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [ai.data?.status, ai.reload]);
  const sleepItems = [...(ai.data?.recommendations ?? []), ...(ai.data?.insights ?? [])].filter((item) => item.scope === "sleep" && item.evidenceIds.includes("sleep.duration7d") && item.evidenceIds.includes("sleep.coverage7d"));
  const sleepEnd = ai.data?.evidence["sleep.coverage7d"]?.observedOn;
  const sleepStart = sleepEnd ? new Date(Date.parse(sleepEnd) - 6 * 86_400_000).toISOString() : null;
  const points = series.data?.points ?? [];
  const hourlyHeartRate = series.data?.heartRateHourly ?? [];
  const summary = series.data?.summary;

  return (
    <>
      <PageHeader
        eyebrow="Сон и восстановление"
        title="Восстановление"
        description="Продолжительность и стадии сна, дневной пульс с часов, пульс покоя, HRV и другие показатели, из доступных данных Xiaomi Cloud и Health Connect. У каждого показателя своя дата последнего измерения."
        actions={<a className="button button--secondary" href={csvUrl("recovery", period)} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели восстановления">
        <KpiCard label="Последний сон" value={duration(summary?.sleepMinutes)} hint={summary?.sleepDate ? formatDate(summary.sleepDate) : "Нет данных"} icon="clock" tone="violet" featured />
        <KpiCard label="Средний пульс с часов" value={summary?.averageHeartRateBpm == null ? "—" : `${formatNumber(summary.averageHeartRateBpm, 0)} уд/мин`} hint={`${summary?.heartRateDate ? formatDate(summary.heartRateDate) + " · " : ""}${heartRateRange(summary?.minimumHeartRateBpm, summary?.maximumHeartRateBpm)}`} icon="heart" tone="coral" />
        <KpiCard label="Пульс покоя" value={summary?.restingHeartRateBpm == null ? "—" : `${formatNumber(summary.restingHeartRateBpm, 0)} уд/мин`} hint={`${summary?.restingHeartRateDate ? formatDate(summary.restingHeartRateDate) + " · " : ""}${baselineHint(summary?.restingHeartRateBpm, summary?.baselineRestingHeartRateBpm, "уд/мин")}`} icon="heart" tone="coral" />
        <KpiCard label="HRV · RMSSD" value={summary?.hrvRmssdMs == null ? "—" : `${formatNumber(summary.hrvRmssdMs, 0)} мс`} hint={`${summary?.hrvDate ? formatDate(summary.hrvDate) + " · " : ""}${baselineHint(summary?.hrvRmssdMs, summary?.baselineHrvRmssdMs, "мс")}`} icon="activity" tone="green" />
        <KpiCard label="SpO₂" value={summary?.spo2Pct == null ? "—" : `${formatNumber(summary.spo2Pct)}%`} hint={summary?.spo2Date ? `Замер ${formatDate(summary.spo2Date)}` : "Показывается только при наличии"} icon="progress" tone="blue" />
      </section>

      <section className="panel sleep-analysis" aria-labelledby="sleep-analysis-title">
        <div className="panel__head"><div><span className="eyebrow">Локальный Codex</span><h2 id="sleep-analysis-title">Разбор сна за неделю</h2><p>{sleepStart && sleepEnd ? `${formatDate(sleepStart)} — ${formatDate(sleepEnd)} · ${ai.data?.status === "stale" ? "сохранённый разбор устарел" : "по сохранённым данным"}` : "Последние 7 московских дней, включая сегодня"}</p></div></div>
        {sleepItems.length && (ai.data?.status === "fresh" || ai.data?.status === "stale") ? <>
          <div className="sleep-analysis__items">{sleepItems.map((item) => <article key={item.id}><h3>{item.title}</h3><p>{item.text}</p><EvidenceChips evidenceIds={item.evidenceIds} evidence={ai.data!.evidence} /></article>)}</div>
          <p className="chart-note">Разбор от {formatDateTime(ai.data.generatedAt)} · информационная поддержка, не медицинское заключение. Продолжительность сна не определяет его качество полностью.</p>
        </> : ai.loading ? <LoadingState compact /> : ai.error ? <ErrorState message={ai.error.message} onRetry={ai.reload} /> : <p className="chart-note">{ai.data?.status === "pending" ? "Разбор сна готовится в фоне. Доступные измерения показаны на графиках ниже." : "Готового разбора сна за неделю пока нет. Доступные измерения показаны ниже."}</p>}
      </section>

      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : points.length ? (
        <>
          <ChartCard title="Сон" subtitle="Общая продолжительность и доступные стадии" option={sleepChartOption(points)} ariaLabel="График продолжительности и стадий сна" height={390} />
          {hourlyHeartRate.length > 0 && (
            <WatchHeartRateChart points={hourlyHeartRate} />
          )}
          {(points.some((point) => point.restingHeartRateBpm !== null) || points.some((point) => point.hrvRmssdMs !== null)) && (
            <ChartCard title="Пульс покоя и HRV" subtitle="Два независимых показателя относительно собственной динамики" option={recoveryChartOption(points)} ariaLabel="График пульса покоя и вариабельности ритма" height={390} />
          )}
          <details className="panel recovery-table data-table-wrap">
            <summary>Таблица показателей восстановления</summary>
            <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Сон</th><th>Глубокий</th><th>REM</th><th>Средний пульс</th><th>Мин.</th><th>Макс.</th><th>Пульс покоя</th><th>HRV</th><th>SpO₂</th></tr></thead><tbody>
              {[...points].reverse().map((point) => <tr key={point.measuredAt}><td>{formatDate(point.measuredAt)}</td><td>{duration(point.sleepMinutes)}</td><td>{duration(point.deepSleepMinutes)}</td><td>{duration(point.remSleepMinutes)}</td><td>{formatNumber(point.averageHeartRateBpm, 0)}</td><td>{formatNumber(point.minimumHeartRateBpm, 0)}</td><td>{formatNumber(point.maximumHeartRateBpm, 0)}</td><td>{formatNumber(point.restingHeartRateBpm, 0)}</td><td>{formatNumber(point.hrvRmssdMs, 0)}</td><td>{point.spo2Pct == null ? "—" : `${formatNumber(point.spo2Pct)}%`}</td></tr>)}
            </tbody></table></div>
          </details>
          {series.data?.correlations.length ? <CorrelationPanel
            id="recovery-correlations"
            correlations={series.data.correlations}
            metricLabels={metricLabels}
          /> : null}
        </>
      ) : <EmptyState title="Данных сна и восстановления пока нет" text="Показатели появятся после синхронизации доступной истории Xiaomi Cloud или Health Connect." />}

      <aside className="info-note"><Icon name="heart" /><p>Показатели предназначены для наблюдения за личной динамикой и не являются медицинской оценкой или рекомендацией.</p></aside>
    </>
  );
}
