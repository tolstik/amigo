import { useCallback } from "react";
import { useOutletContext } from "react-router-dom";
import type { OverviewContext } from "../App";
import { api, csvUrl } from "../api/client";
import { monthlyChangeChartOption, weeklyChangeChartOption, weeklyWeightChartOption, weightChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { MonthlyWeightTable, WeeklyWeightTable, WeightTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDelta, formatKg } from "../lib/format";

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
        description={`На этом экране учитываются только измерения с ${formatDate(plan?.startDate)}. Необычные замеры видны, но не влияют на тренд.`}
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
      ) : series.data ? (
        <>
          {series.data.points.length ? (
            <ChartCard
              title="Вес относительно плана"
              subtitle={`${series.data.meta.count} дней с замерами · московское время`}
              option={weightChartOption(series.data.points, true, series.data.projection, series.data.planProjection)}
              ariaLabel={`График прогресса веса, плана и прогноза с ${formatDate(plan?.startDate)}`}
              height={450}
              footer={<WeightTable points={series.data.points} />}
            />
          ) : <EmptyState title="Замеров программы пока нет" text="После следующей синхронизации здесь появятся точки веса, план и тренд." />}

          {series.data.weekly.length ? (
            <>
              <ChartCard
                title="Вес по неделям"
                subtitle="Последний вес · план на дату и на конец недели · минимум — отдельной линией"
                option={weeklyWeightChartOption(series.data.weekly)}
                ariaLabel="Недельный график последнего веса, плана на дату и на конец недели с линией минимального веса"
                height={410}
              />
              <ChartCard
                title="Изменение по неделям"
                subtitle="Фактическое изменение · план на дату · план на всю неделю · снижение ниже нуля"
                option={weeklyChangeChartOption(series.data.weekly)}
                ariaLabel="Недельный график изменения веса: факт, план на дату и на всю неделю"
                height={390}
                footer={<><PeriodChangeHelp /><WeeklyWeightTable points={series.data.weekly} /></>}
              />
            </>
          ) : series.data.points.length ? (
            <EmptyState title="Недельная сводка пока не готова" text="Она появится после обновления аналитики программы." />
          ) : null}

          {series.data.monthly.length ? (
            <ChartCard
              title="Изменение по месяцам"
              subtitle="Фактическое изменение · план на дату · план на весь месяц · снижение ниже нуля"
              option={monthlyChangeChartOption(series.data.monthly)}
              ariaLabel="Месячный график изменения веса: факт, план на дату и на весь месяц"
              height={390}
              footer={<>
                <PeriodChangeHelp />
                <MonthlyWeightTable points={series.data.monthly} />
              </>}
            />
          ) : null}
        </>
      ) : null}

      <section className="explain-grid">
        <article className="panel explain-card"><span className="explain-card__number">01</span><div><h3>Сглаженный тренд</h3><p>Показывает направление без суточного шума. Для дня с несколькими замерами используется медиана.</p></div></article>
        <article className="panel explain-card"><span className="explain-card__number">02</span><div><h3>Устойчивый прогноз</h3><p>Рассчитывается по последним 42 дням. До накопления достаточной истории дата цели не обещается.</p></div></article>
        <article className="panel explain-card"><span className="explain-card__number">03</span><div><h3>Необычные точки</h3><p>Ромбами отмечены выбросы. Они остаются в истории, но не искажают аналитический тренд.</p></div></article>
      </section>
    </>
  );
}

function PeriodChangeHelp() {
  return <>
    <p className="chart-note">Полный план фиксирован; план на дату — за прошедшую часть периода. Темп −4 кг между 15-ми числами сохраняется; календарные периоды могут пересекать месяцы программы разной длины.</p>
    <details className="data-table-wrap">
      <summary>Как считаются факт и план</summary>
      <div className="chart-note">
        <p>Факт — разница дневных медиан веса без выбросов. Берётся замер за день до начала периода и последний внутри него. Если замера на границе нет, сравниваются первый и последний замеры внутри периода; один замер не задаёт изменение. Первый период считается от 127,03 кг с 15 августа. Точные даты и веса — в подсказке и таблице.</p>
        <p>Для завершённого периода план на дату равен полному. Цвет факта сравнивает снижение с планом за те же даты замеров: зелёный — темп выполнен, жёлтый — снижение медленнее, коралловый — снижения нет. Пропуски и незавершённые периоды не экстраполируются.</p>
      </div>
    </details>
  </>;
}
