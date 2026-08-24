import { useCallback, useState } from "react";
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
import { EvidenceChips } from "../components/EvidenceChips";
import { TaskDialog, type TaskDialogSource } from "../components/TaskDialog";
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
  const loadAi = useCallback((signal: AbortSignal) => api.aiAnalysis(signal), []);
  const loadActivity = useCallback((signal: AbortSignal) => api.activity("30d", signal), []);
  const loadRecovery = useCallback((signal: AbortSignal) => api.recovery("30d", signal), []);
  const preview = useApi(loadPreview);
  const ai = useApi(loadAi);
  const activity = useApi(loadActivity);
  const recovery = useApi(loadRecovery);
  const [taskSource, setTaskSource] = useState<TaskDialogSource | null>(null);
  const [taskNotice, setTaskNotice] = useState<string | null>(null);

  if (overview.loading && !overview.data) return <LoadingState />;
  if (overview.error && !overview.data) return <ErrorState message={overview.error.message} onRetry={overview.reload} />;
  if (!overview.data) return null;

  const { weight, plan, pressure, composition } = overview.data;
  const progress = weight.progressPct;
  const aiItems = ai.data
    ? [
        ...ai.data.recommendations.map((item) => ({ ...item, kind: "recommendation" as const })),
        ...ai.data.insights.map((item) => ({ ...item, kind: "insight" as const })),
      ]
    : [];

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
            <span><small>Сон</small><strong>{recovery.data?.summary.sleepMinutes == null ? "—" : `${Math.floor(recovery.data.summary.sleepMinutes / 60)} ч ${Math.round(recovery.data.summary.sleepMinutes % 60)} мин`}</strong><em>{recovery.data?.summary.latestDate ? formatDate(recovery.data.summary.latestDate) : "Ожидаем Health Connect"}</em></span>
            <Icon name="arrow" />
          </Link>
          {pressure.latestPulse !== null && <p className="today-panel__note">Пульс в последней сессии: <strong>{formatNumber(pressure.latestPulse, 0)} уд/мин</strong></p>}
        </article>
      </section>

      <section className="insights-section" aria-labelledby="insights-title">
        <div className="section-heading">
          <div><span className="eyebrow">Персональный разбор</span><h2 id="insights-title">ИИ-анализ</h2></div>
          <span className={`rules-badge rules-badge--${ai.data?.status ?? "pending"}`}><Icon name="sparkle" /> {ai.data?.generatedAt ? `${ai.data.status === "stale" ? "Устарел · " : ""}${formatDateTime(ai.data.generatedAt)}` : "Готовится"}</span>
        </div>
        {ai.loading && !ai.data ? <LoadingState compact /> : ai.error && !ai.data ? (
          <div className="ai-unavailable"><strong>ИИ-анализ временно недоступен</strong><p>Числовые показатели продолжают рассчитываться без модели.</p></div>
        ) : ai.data?.status === "unavailable" || ai.data?.status === "pending" ? (
          <div className="ai-unavailable"><strong>{ai.data.status === "pending" ? "Анализ новых данных готовится" : "ИИ-анализ временно недоступен"}</strong><p>Здесь нет шаблонной подмены: до готовности модели остаются только проверяемые факты.</p></div>
        ) : (
          <div className="ai-analysis panel">
            <div className="ai-analysis__intro"><span className="ai-orbit"><Icon name="sparkle" /></span><div><h3>{ai.data?.headline ?? "Разбор текущей динамики"}</h3>{ai.data?.summary && <p>{ai.data.summary}</p>}<small>Данные на {formatDateTime(ai.data?.dataAsOf)} · {ai.data?.model ?? "Codex"} · информационная поддержка, не диагноз и не замена врачу</small></div></div>
            {aiItems.length > 0 && <div className="insight-grid">
              {aiItems.slice(0, 6).map((item) => (
                <article className={`insight ${item.kind === "recommendation" ? "insight--recommendation" : ""}`} key={`${item.kind}-${item.id}`}>
                  <span className="insight__icon"><Icon name={item.kind === "recommendation" ? "progress" : "activity"} /></span>
                  <div className="insight__body"><strong>{item.title}</strong><p>{item.text}</p><EvidenceChips evidenceIds={item.evidenceIds} evidence={ai.data?.evidence ?? {}} />{item.kind === "recommendation" && ai.data?.analysisId !== null && ai.data?.analysisId !== undefined && <button className="insight__task" type="button" onClick={() => setTaskSource({ analysisId: ai.data!.analysisId!, itemId: item.id, title: item.title, text: item.text })}>Создать задачу</button>}</div>
                </article>
              ))}
            </div>}
            {ai.data?.limitations.length ? <p className="ai-analysis__limitations">Ограничения: {ai.data.limitations.join(" · ")}</p> : null}
          </div>
        )}
      </section>

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
      <div className="sr-status" aria-live="polite">{taskNotice}</div>
      {taskSource && <TaskDialog initial={{ title: taskSource.title, note: taskSource.text }} source={taskSource} onSubmit={async (input) => { await api.createTask(input); setTaskNotice("Задача создана и доступна в разделе «Задачи»."); }} onClose={() => setTaskSource(null)} />}
    </>
  );
}
