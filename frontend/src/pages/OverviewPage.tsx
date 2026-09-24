import { lazy, Suspense, useCallback } from "react";
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

const BodyModel = lazy(() => import("../components/BodyModel"));

function planPosition(deviation: number | null): string {
  if (deviation === null) return "Появится после нового замера";
  if (Math.abs(deviation) < 0.05) return "Точно по плану";
  return deviation < 0 ? `${formatNumber(Math.abs(deviation))} кг впереди плана` : `${formatNumber(deviation)} кг выше плана`;
}

export function OverviewPage() {
  const overview = useOutletContext<OverviewContext>();
  const loadPreview = useCallback((signal: AbortSignal) => api.weight("90d", signal), []);
  const loadActivity = useCallback((signal: AbortSignal) => api.activity("30d", signal), []);
  const loadRecovery = useCallback((signal: AbortSignal) => api.recovery("30d", signal), []);
  const preview = useApi(loadPreview);
  const activity = useApi(loadActivity);
  const recovery = useApi(loadRecovery);
  const loadProfile = useCallback((signal: AbortSignal) => api.profile(signal), []);
  const profile = useApi(loadProfile);

  if (overview.loading && !overview.data) return <LoadingState />;
  if (overview.error && !overview.data) return <ErrorState message={overview.error.message} onRetry={overview.reload} />;
  if (!overview.data) return null;

  const { weight, plan, pressure, composition } = overview.data;
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
          value={formatKg(weight.latestKg, 2)}
          hint={weight.latestAt ? `Замер ${formatDateTime(weight.latestAt)}` : "Ждём первый замер"}
          icon="scale"
          tone="green"
          featured
        />
        <KpiCard
          label="С начала программы"
          value={formatDelta(weight.changeSinceStartKg, "кг", 2)}
          hint={`Старт ${formatKg(plan.startWeightKg, 2)}${weight.latestAt ? ` · Замер ${formatDateTime(weight.latestAt)}` : ""}`}
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
            <div><span className="eyebrow">Движение к цели</span><h2>План и факт</h2></div>
            <span className="goal-panel__target">Цель <strong>{formatKg(plan.targetWeightKg)}</strong></span>
          </div>
          <div className="goal-progress">
            <div className="goal-progress__label"><strong>Факт</strong><span>{progress === null ? "Нет данных" : formatPercent(progress, 1)}</span></div>
            <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress === null ? undefined : clampProgress(progress)} aria-valuetext={progress === null ? "Нет данных" : formatPercent(progress, 1)} aria-label="Факт по последнему весу">
              <span style={{ width: `${clampProgress(progress)}%` }} />
            </div>
            <small>{weight.latestAt ? `Последний замер ${formatDateTime(weight.latestAt)}` : "Ждём первый замер"}</small>
          </div>
          <div className="goal-progress goal-progress--plan">
            <div className="goal-progress__label"><strong>План на сегодня</strong><span>{plan.progressTodayPct === null ? "Нет данных" : formatPercent(plan.progressTodayPct, 1)}</span></div>
            <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={plan.progressTodayPct === null ? undefined : clampProgress(plan.progressTodayPct)} aria-valuetext={plan.progressTodayPct === null ? "Нет данных" : formatPercent(plan.progressTodayPct, 1)} aria-label="План на сегодня">
              <span style={{ width: `${clampProgress(plan.progressTodayPct)}%` }} />
            </div>
            <small>{formatDate(overview.data.generatedAt)} · {formatKg(plan.plannedTodayKg)}</small>
          </div>
          <div className="goal-metrics">
            <div><span>По плану сегодня</span><strong>{formatKg(plan.plannedTodayKg)}</strong></div>
            <div><span>Положение</span><strong>{weight.isStale ? "Нужен свежий замер: последнему больше 14 дней" : planPosition(weight.latestDeviationFromPlanKg)}</strong></div>
            <div><span>Тренд за 28 дней</span><strong>{formatDelta(weight.trend28dKg)}</strong></div>
            <div><span>Ожидаемая дата цели</span><strong>{weight.forecastDate ? formatDate(weight.forecastDate) : "Недостаточно данных"}</strong></div>
          </div>
          <Link className="text-link" to="/progress">Подробный прогресс <Icon name="arrow" /></Link>
        </article>

        <article className="panel today-panel">
          <div className="panel__head"><div><span className="eyebrow">Последние данные</span><h2>Здоровье и активность</h2></div></div>
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
          <Link className="today-row" to="/activity">
            <span className="today-row__icon today-row__icon--green"><Icon name="activity" /></span>
            <span><small>Шаги · Xiaomi Cloud</small><strong>{activity.data?.summary.steps == null ? "—" : `${formatNumber(activity.data.summary.steps, 0)} шагов`}</strong><em>{activity.data?.summary.steps == null ? "Нет данных Xiaomi Cloud" : activity.data.summary.latestDate ? formatDate(activity.data.summary.latestDate) : "Дата Xiaomi Cloud неизвестна"}</em></span>
            <Icon name="arrow" />
          </Link>
          <Link className="today-row" to="/recovery">
            <span className="today-row__icon today-row__icon--blue"><Icon name="clock" /></span>
            <span><small>Сон</small><strong>{recovery.data?.summary.sleepMinutes == null ? "—" : `${Math.floor(recovery.data.summary.sleepMinutes / 60)} ч ${Math.round(recovery.data.summary.sleepMinutes % 60)} мин`}</strong><em>{recovery.data?.summary.sleepDate ? formatDate(recovery.data.summary.sleepDate) : "Нет данных сна"}</em></span>
            <Icon name="arrow" />
          </Link>
          {pressure.latestPulse !== null && <p className="today-panel__note">Пульс в последней сессии: <strong>{formatNumber(pressure.latestPulse, 0)} уд/мин</strong></p>}
        </article>
      </section>

      {preview.data?.points.length ? (
        <ChartCard
          title="Последние 90 дней"
          subtitle="Дневные медианы, сглаженный тренд и линия плана · необычные замеры скрыты"
          option={weightChartOption(preview.data.points.filter((point) => !point.isOutlier), false, preview.data.projection, preview.data.planProjection, true)}
          ariaLabel="График веса за последние 90 дней"
          height={330}
          aside={<Link className="text-link" to="/progress#history">Вся история <Icon name="arrow" /></Link>}
          footer={<WeightTable points={preview.data.points} />}
        />
      ) : preview.loading ? <LoadingState compact /> : preview.error ? (
        <ErrorState message={preview.error.message} onRetry={preview.reload} />
      ) : null}
      {profile.data && <Suspense fallback={<LoadingState compact />}><BodyModel latestKg={weight.latestKg} heightCm={profile.data.height_cm} startKg={plan.startWeightKg} targetKg={plan.targetWeightKg} /></Suspense>}
    </>
  );
}
