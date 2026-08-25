import { useCallback } from "react";
import { api, csvUrl } from "../api/client";
import { activityDailyChartOption, weeklyActivityChartOption } from "../charts/options";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { CorrelationPanel } from "../components/CorrelationPanel";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";

const metricLabels: Record<string, string> = {
  steps: "Шаги",
  active_minutes: "Активные минуты",
  weight_kg: "Вес",
  systolic_mm_hg: "Систолическое давление",
};

function stepsComparison(actual: number | null, baseline: number | null): string {
  if (actual === null) return "Нет данных Xiaomi Cloud";
  if (baseline === null || baseline === 0) return "База появится после 28 полных дней Xiaomi Cloud";
  const delta = ((actual - baseline) / baseline) * 100;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${formatNumber(delta, 0)}% к личной базе`;
}

export function ActivityPage() {
  const [period, setPeriod] = useChartPeriod("90d");
  const load = useCallback((signal: AbortSignal) => api.activity(period, signal), [period]);
  const series = useApi(load);
  const points = series.data?.points ?? [];
  const weekly = series.data?.weekly ?? [];
  const summary = series.data?.summary;

  return (
    <>
      <PageHeader
        eyebrow="Xiaomi Cloud"
        title="Активность"
        description="Шаги поступают только напрямую из Xiaomi Cloud и не подменяются Health Connect. Недельный факт сравнивается с личной базой предыдущих 28 полных дней."
        actions={<a className="button button--secondary" href={csvUrl("activity", period)} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели активности">
        <KpiCard label="Шаги" value={formatNumber(summary?.steps, 0)} hint={stepsComparison(summary?.steps ?? null, summary?.baselineSteps ?? null)} icon="activity" tone="green" featured />
        <KpiCard label="Дистанция" value={summary?.distanceKm == null ? "—" : `${formatNumber(summary.distanceKm)} км`} hint={summary?.latestDate ? formatDate(summary.latestDate) : "Нет данных"} icon="progress" tone="blue" />
        <KpiCard label="Активные минуты" value={summary?.activeMinutes == null ? "—" : `${formatNumber(summary.activeMinutes, 0)} мин`} hint="За последний доступный день" icon="clock" tone="violet" />
        <KpiCard label="Тренировки, 7 дней" value={formatNumber(summary?.workouts7d ?? 0, 0)} hint={summary?.dataAsOf ? `Данные на ${formatDateTime(summary.dataAsOf)}` : "Ожидаем синхронизацию"} icon="calendar" tone="coral" />
      </section>

      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : points.length ? (
        <>
          <ChartCard
            title="Активность по дням"
            subtitle="Шаги и активные минуты; незаполненные дни не интерполируются"
            option={activityDailyChartOption(points)}
            ariaLabel="График шагов и активных минут по дням"
            height={390}
            footer={
              <details className="data-table-wrap">
                <summary>Таблица дневной активности</summary>
                <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Шаги</th><th>Км</th><th>Активность</th><th>Тренировки</th></tr></thead><tbody>
                  {[...points].reverse().map((point) => <tr key={point.measuredAt}><td>{formatDate(point.measuredAt)}</td><td>{point.steps === null ? "Нет данных Xiaomi Cloud" : formatNumber(point.steps, 0)}</td><td>{formatNumber(point.distanceKm)}</td><td>{point.activeMinutes == null ? "—" : `${formatNumber(point.activeMinutes, 0)} мин`}</td><td>{point.workouts}</td></tr>)}
                </tbody></table></div>
              </details>
            }
          />
          {weekly.length > 0 && <ChartCard
            title="Недели: факт и личная база"
            subtitle="База учитывает те же дни недели за предыдущие 28 полных дней"
            option={weeklyActivityChartOption(weekly)}
            ariaLabel="Недельные столбики шагов: факт и личная база"
            height={390}
            footer={
              <details className="data-table-wrap"><summary>Таблица недельной активности</summary><div className="data-table-scroll"><table className="data-table"><thead><tr><th>Неделя</th><th>Факт</th><th>База</th><th>Дней</th><th>Статус</th></tr></thead><tbody>
                {[...weekly].reverse().map((point) => <tr key={point.startDate}><td>{formatDate(point.startDate)} — {formatDate(point.endDate)}</td><td>{point.actualSteps === null ? "Нет данных Xiaomi Cloud" : formatNumber(point.actualSteps, 0)}</td><td>{point.baselineSteps === null ? "Нет базы Xiaomi Cloud" : formatNumber(point.baselineSteps, 0)}</td><td>{point.coverageDays}</td><td>{point.isPartial ? "Неполная" : "Полная"}</td></tr>)}
              </tbody></table></div></details>
            }
          />}
          {series.data?.correlations.length ? <CorrelationPanel
            id="activity-correlations"
            correlations={series.data.correlations}
            metricLabels={metricLabels}
          /> : null}
        </>
      ) : <EmptyState title="Данных активности пока нет" text="Подключите Xiaomi Cloud в Amigo Sync. Если прямых данных о шагах нет, Amigo не подставляет ноль или историю Health Connect." />}
    </>
  );
}
