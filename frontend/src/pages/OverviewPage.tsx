import { useCallback } from "react";
import { Link, useOutletContext } from "react-router-dom";
import type { OverviewContext } from "../App";
import { api, csvUrl } from "../api/client";
import { weightChartOption } from "../charts/options";
import { ChartCard } from "../components/ChartCard";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { WeightTable } from "../components/DataTables";
import { useApi } from "../hooks/useApi";
import { clampProgress, formatDate, formatDateTime, formatDelta, formatKg, formatNumber, formatPercent } from "../lib/format";

function planPosition(deviation: number | null): string {
  if (deviation === null) return "Появится после нового замера";
  if (Math.abs(deviation) < 0.05) return "Точно по плану";
  return deviation < 0 ? `${formatNumber(Math.abs(deviation))} кг впереди плана` : `${formatNumber(deviation)} кг выше плана`;
}

export function OverviewPage() {
  const overview = useOutletContext<OverviewContext>();
  const loadPreview = useCallback((signal: AbortSignal) => api.weight("90d", signal), []);
  const loadInsights = useCallback((signal: AbortSignal) => api.insights(signal), []);
  const preview = useApi(loadPreview);
  const insightQuery = useApi(loadInsights);

  if (overview.loading && !overview.data) return <LoadingState />;
  if (overview.error && !overview.data) return <ErrorState message={overview.error.message} onRetry={overview.reload} />;
  if (!overview.data) return null;

  const { weight, plan, pressure, composition } = overview.data;
  const insights = overview.data.insights.length ? overview.data.insights : (insightQuery.data ?? []);
  const progress = weight.progressPct;

  return (
    <>
      <PageHeader
        eyebrow="Ваш путь"
        title="Добрый день! Вот как идут дела"
        description={`План начался ${formatDate(plan.startDate)}. Все изменения и прогнозы считаются только от этой даты.`}
        actions={
          <a className="button button--secondary" href={csvUrl("weight", "program")} download>
            <Icon name="download" /> Скачать CSV
          </a>
        }
      />

      <section className="kpi-grid" aria-label="Главные показатели">
        <KpiCard
          label="Последний вес"
          value={formatKg(weight.latestKg)}
          hint={weight.latestAt ? `Замер ${formatDateTime(weight.latestAt)}` : "Ждём первый замер"}
          icon="scale"
          tone="green"
          featured
        />
        <KpiCard
          label="С начала программы"
          value={formatDelta(weight.changeSinceStartKg)}
          hint={`Стартовый вес ${formatKg(plan.startWeightKg)}`}
          icon="progress"
          tone="blue"
        />
        <KpiCard
          label="Сглаженный вес"
          value={formatKg(weight.smoothed7dKg)}
          hint="Медианный тренд за 7 дней"
          icon="activity"
          tone="violet"
        />
        <KpiCard
          label="Регулярность"
          value={weight.measurementDays30d === null ? "—" : `${formatNumber(weight.measurementDays30d, 0)} дней`}
          hint="Дни с недавними замерами"
          icon="calendar"
          tone="coral"
        />
      </section>

      <section className="overview-columns">
        <article className="panel goal-panel">
          <div className="panel__head">
            <div><span className="eyebrow">Движение к цели</span><h2>{formatPercent(progress)} пути</h2></div>
            <span className="goal-panel__target">Цель <strong>{formatKg(plan.targetWeightKg)}</strong></span>
          </div>
          <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(clampProgress(progress))} aria-label="Прогресс к целевому весу">
            <span style={{ width: `${clampProgress(progress)}%` }} />
          </div>
          <div className="goal-metrics">
            <div><span>По плану сегодня</span><strong>{formatKg(plan.plannedTodayKg)}</strong></div>
            <div><span>Положение</span><strong>{planPosition(weight.deviationFromPlanKg)}</strong></div>
            <div><span>Тренд за 28 дней</span><strong>{formatDelta(weight.trend28dKg)}</strong></div>
            <div><span>Ожидаемая дата цели</span><strong>{weight.forecastDate ? formatDate(weight.forecastDate) : "Недостаточно данных"}</strong></div>
          </div>
          <Link className="text-link" to="/progress">Подробный прогресс <Icon name="arrow" /></Link>
        </article>

        <article className="panel today-panel">
          <div className="panel__head"><div><span className="eyebrow">Последние данные</span><h2>Давление и состав</h2></div></div>
          <Link className="today-row" to="/pressure">
            <span className="today-row__icon today-row__icon--coral"><Icon name="heart" /></span>
            <span><small>Давление</small><strong>{pressure.latestSystolic === null ? "—" : `${formatNumber(pressure.latestSystolic, 0)} / ${formatNumber(pressure.latestDiastolic, 0)}`}</strong><em>{pressure.latestAt ? formatDateTime(pressure.latestAt) : "Нет данных"}</em></span>
            <Icon name="arrow" />
          </Link>
          <Link className="today-row" to="/composition">
            <span className="today-row__icon today-row__icon--violet"><Icon name="composition" /></span>
            <span><small>Доля жира · BIA-оценка</small><strong>{composition.fatPct === null ? "—" : `${formatNumber(composition.fatPct)}%`}</strong><em>{composition.measuredAt ? formatDateTime(composition.measuredAt) : "Нет данных"}</em></span>
            <Icon name="arrow" />
          </Link>
          {pressure.latestPulse !== null && <p className="today-panel__note">Пульс в последней сессии: <strong>{formatNumber(pressure.latestPulse, 0)} уд/мин</strong></p>}
        </article>
      </section>

      {insights.length > 0 && (
        <section className="insights-section" aria-labelledby="insights-title">
          <div className="section-heading"><div><span className="eyebrow">По вашим трендам</span><h2 id="insights-title">Наблюдения</h2></div><span className="rules-badge"><Icon name="sparkle" /> Без «чёрного ящика»</span></div>
          <div className="insight-grid">
            {insights.slice(0, 3).map((insight) => (
              <article className={`insight insight--${insight.tone}`} key={insight.id}>
                <span className="insight__icon"><Icon name={insight.tone === "achievement" ? "sparkle" : "activity"} /></span>
                <div><strong>{insight.title}</strong><p>{insight.text}</p></div>
              </article>
            ))}
          </div>
        </section>
      )}

      {preview.data?.points.length ? (
        <ChartCard
          title="Последние 90 дней"
          subtitle="Дневные медианы, сглаженный тренд и линия плана"
          option={weightChartOption(preview.data.points, false, preview.data.projection, preview.data.planProjection)}
          ariaLabel="График веса за последние 90 дней"
          height={330}
          aside={<Link className="text-link" to="/history">Вся история <Icon name="arrow" /></Link>}
          footer={<WeightTable points={preview.data.points} />}
        />
      ) : preview.loading ? <LoadingState compact /> : preview.error ? (
        <ErrorState message={preview.error.message} onRetry={preview.reload} />
      ) : null}
    </>
  );
}
