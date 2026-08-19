import { useCallback } from "react";
import { useOutletContext } from "react-router-dom";
import type { OverviewContext } from "../App";
import { api, csvUrl } from "../api/client";
import { weightChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { WeightTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDelta, formatKg, formatNumber } from "../lib/format";

export function ProgressPage() {
  const overview = useOutletContext<OverviewContext>();
  const loadSeries = useCallback((signal: AbortSignal) => api.weight("program", signal), []);
  const series = useApi(loadSeries);
  const weight = overview.data?.weight;
  const plan = overview.data?.plan;

  return (
    <>
      <PageHeader
        eyebrow="Программа похудения"
        title="Прогресс и прогноз"
        description="На этом экране учитываются только измерения с 15 августа 2026 года. Необычные замеры видны, но не влияют на тренд."
        actions={<a className="button button--secondary" href={csvUrl("weight", "program")} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели программы">
        <KpiCard label="Сейчас" value={formatKg(weight?.latestKg)} hint={`Тренд: ${formatKg(weight?.smoothed7dKg)}`} icon="scale" tone="green" featured />
        <KpiCard label="От плана" value={formatDelta(weight?.deviationFromPlanKg)} hint={`Сегодня по плану ${formatKg(plan?.plannedTodayKg)}`} icon="progress" tone="blue" />
        <KpiCard label="Прогноз цели" value={weight?.forecastDate ? formatDate(weight.forecastDate) : "Пока рано"} hint="Показывается только при устойчивом снижении" icon="calendar" tone="violet" />
        <KpiCard label="Цель по плану" value={formatDate(plan?.targetDate)} hint="Темп −4 кг за календарный месяц" icon="progress" tone="coral" />
      </section>

      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : series.data?.points.length ? (
        <ChartCard
          title="Вес относительно плана"
          subtitle={`${series.data.meta.count} дней с замерами · московское время`}
          option={weightChartOption(series.data.points, true, series.data.projection, series.data.planProjection)}
          ariaLabel="График прогресса веса, плана и прогноза с 15 августа 2026 года"
          height={450}
          footer={<WeightTable points={series.data.points} />}
        />
      ) : <EmptyState title="Замеров программы пока нет" text="После следующей синхронизации здесь появятся точки веса, план и тренд." />}

      <section className="explain-grid">
        <article className="panel explain-card"><span className="explain-card__number">01</span><div><h3>Сглаженный тренд</h3><p>Показывает направление без суточного шума. Для дня с несколькими замерами используется медиана.</p></div></article>
        <article className="panel explain-card"><span className="explain-card__number">02</span><div><h3>Устойчивый прогноз</h3><p>Рассчитывается по последним 42 дням. До накопления достаточной истории дата цели не обещается.</p></div></article>
        <article className="panel explain-card"><span className="explain-card__number">03</span><div><h3>Необычные точки</h3><p>Ромбами отмечены выбросы. Они остаются в истории, но не искажают аналитический тренд.</p></div></article>
      </section>
    </>
  );
}
