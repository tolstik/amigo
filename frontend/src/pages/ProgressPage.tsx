import { useCallback } from "react";
import { useOutletContext } from "react-router-dom";
import type { OverviewContext } from "../App";
import { api, csvUrl } from "../api/client";
import { bmiChartOption, planDeviationChartOption, monthlyChangeChartOption, weeklyChangeChartOption, weeklyWeightChartOption, weightChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { MonthlyWeightTable, WeeklyWeightTable, WeightTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { WeightCandlestickChart } from "../components/WeightCandlestickChart";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useChartPeriod } from "../hooks/useChartPeriod";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDelta, formatKg } from "../lib/format";

export function ProgressPage() {
  const overview = useOutletContext<OverviewContext>();
  const loadSeries = useCallback((signal: AbortSignal) => api.weight("program", signal), []);
  const series = useApi(loadSeries);
  const [historyPeriod, setHistoryPeriod] = useChartPeriod("all");
  const loadHistory = useCallback((signal: AbortSignal) => api.weight(historyPeriod, signal), [historyPeriod]);
  const history = useApi(loadHistory);
  // All raw history establishes the preceding candle before the 90-day display trim.
  const loadRaw = useCallback((signal: AbortSignal) => api.weight("all", signal), []);
  const raw = useApi(loadRaw);
  const loadProfile = useCallback((signal: AbortSignal) => api.profile(signal), []);
  const profile = useApi(loadProfile);
  const weight = overview.data?.weight;
  const plan = overview.data?.plan;

  return (
    <>
      <PageHeader
        eyebrow="Программа похудения"
        title="Прогресс и прогноз"
        description={`Показатели программы считаются с ${formatDate(plan?.startDate)}. Полная история и дневные изменения — на отдельных графиках ниже.`}
        actions={<a className="button button--secondary" href={csvUrl("weight", "program")} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели программы">
        <KpiCard label="Сейчас" value={formatKg(weight?.latestKg)} hint={`Тренд: ${formatKg(weight?.smoothed7dKg)}`} icon="scale" tone="green" featured />
        <KpiCard label="От плана" value={formatDelta(weight?.latestDeviationFromPlanKg)} hint={`Последний замер · сегодня по плану ${formatKg(plan?.plannedTodayKg)}`} icon="progress" tone="blue" />
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

          {series.data.points.length > 0 && <>
            <ChartCard title="Отклонение от плана" subtitle="Дневная медиана минус план на ту же дату · плюс — выше плана, минус — ниже" option={planDeviationChartOption(series.data.points)} ariaLabel="График разницы между фактическим весом и планом" height={350} />
            {profile.data && <ChartCard title="Индекс массы тела по дням" subtitle={`Дневная медиана веса / рост в метрах² · рост ${profile.data.height_cm} см · дни без замеров остаются пустыми`} option={bmiChartOption(series.data.points, profile.data.height_cm)} ariaLabel="График ежедневного индекса массы тела" height={350} />}
          </>}

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
                subtitle="Потеря веса положительная: факт · план на дату · план на всю неделю"
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
              subtitle="Потеря веса положительная: факт · план на дату · план на весь месяц"
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

      {raw.data ? <WeightCandlestickChart points={raw.data.raw} asOf={overview.data?.generatedAt ?? new Date().toISOString()} startDate={plan?.startDate} baselineKg={plan?.startWeightKg} /> : raw.error ? <ErrorState message={raw.error.message} onRetry={raw.reload} /> : <LoadingState compact />}
      <section id="history" className="weight-history">
        <div className="section-heading"><div><span className="eyebrow">Архив измерений</span><h2>Вся история веса</h2></div><a className="button button--secondary" href={csvUrl("weight", historyPeriod)} download><Icon name="download" /> CSV истории</a></div>
        <p className="chart-note">Включает замеры до {formatDate(plan?.startDate)}. Они не влияют на показатели программы и прогноз.</p>
        <div className="toolbar"><PeriodSwitcher value={historyPeriod} onChange={setHistoryPeriod} /></div>
        {history.data?.points.length ? <ChartCard title="История измерений" subtitle="Дневные медианы · разрывы длиннее 14 дней не соединяются" option={weightChartOption(history.data.points, false, [], history.data.planProjection)} ariaLabel="График всей истории веса, включая измерения до программы" height={450} footer={<WeightTable points={history.data.points} />} /> : history.error ? <ErrorState message={history.error.message} onRetry={history.reload} /> : history.loading ? <LoadingState compact /> : <EmptyState title="В выбранном периоде нет веса" text="Выберите другой период истории." />}
      </section>

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
    <p className="chart-note">Потеря веса показывается положительным числом, набор веса — отрицательным. Полный план фиксирован; план на дату — за прошедшую часть периода. Темп −4 кг между 15-ми числами сохраняется.</p>
    <details className="data-table-wrap">
      <summary>Как считаются факт и план</summary>
      <div className="chart-note">
        <p>Факт — разница дневных медиан веса без выбросов. Берётся замер за день до начала периода и последний внутри него. Если замера на границе нет, сравниваются первый и последний замеры внутри периода; один замер не задаёт изменение. Первый период считается от 127,03 кг с 15 августа. Точные даты и веса — в подсказке и таблице.</p>
        <p>Для завершённого периода план на дату равен полному. Цвет факта сравнивает положительное снижение с планом за те же даты замеров: зелёный — план выполнен, жёлтый — снижение медленнее, коралловый — снижения нет или вес вырос. Пропуски и незавершённые периоды не экстраполируются.</p>
      </div>
    </details>
  </>;
}
