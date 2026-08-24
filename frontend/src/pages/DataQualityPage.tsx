import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { DataQualityDay, DataQualityMetric, DataQualityRange, DataSourceStatus } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";

const metricLabels: Record<string, string> = {
  weight: "Вес",
  blood_pressure: "Давление",
  body_composition: "Состав тела",
  steps: "Шаги",
  distance: "Дистанция",
  active_calories: "Активные калории",
  total_calories: "Общие калории",
  exercise: "Тренировки",
  sleep: "Сон",
  heart_rate: "Пульс с часов",
  resting_heart_rate: "Пульс покоя",
  hrv_rmssd: "HRV · RMSSD",
  oxygen_saturation: "SpO₂",
  vo2_max: "VO₂ max",
};

const sourceLabels: Record<string, string> = {
  withings: "Withings",
  mi_fitness: "Xiaomi Cloud",
  health_connect: "Health Connect",
};

const sourceStatusLabels: Record<DataSourceStatus, string> = {
  healthy: "Актуален",
  pending: "Синхронизируется",
  delayed: "Задержка",
  error: "Ошибка",
  not_configured: "Не настроен",
};

const stateLabels: Record<DataQualityDay["state"], string> = {
  available: "Есть данные",
  confirmed_empty: "Источник подтвердил отсутствие данных",
  missing: "Пробел",
};

function policyLabel(metric: DataQualityMetric): string {
  if (metric.key === "steps") return "Только Xiaomi Cloud; Health Connect не используется как подмена";
  if (metric.sourcePolicy === "withings_only") return "Только Withings";
  if (metric.sourcePolicy === "finalized_xiaomi_then_health_connect") return "Xiaomi Cloud после активации, затем Health Connect";
  return "Политика источника задана сервером";
}

function dayLabel(day: DataQualityDay): string {
  const source = day.source ? ` · ${sourceLabels[day.source] ?? day.source}` : "";
  return `${formatDate(day.date)} · ${stateLabels[day.state]}${source}`;
}

export function DataQualityPage() {
  const [range, setRange] = useState<DataQualityRange>("30d");
  const [filter, setFilter] = useState<"all" | "attention" | "missing">("all");
  const load = useCallback((signal: AbortSignal) => api.dataQuality(range, signal), [range]);
  const quality = useApi(load);
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroll.current) scroll.current.scrollLeft = scroll.current.scrollWidth; }, [quality.data?.generatedAt, filter]);
  const metrics = useMemo(() => (quality.data?.metrics ?? []).filter((metric) => {
    if (filter === "missing") return metric.coverage.missing > 0;
    if (filter === "attention") return metric.status === "missing" || metric.status === "partial";
    return true;
  }), [filter, quality.data?.metrics]);

  return <>
    <PageHeader eyebrow="Источники и покрытие" title="Качество данных" description="Проверьте, какие дни действительно заполнены, где источник подтвердил отсутствие записей и где остался пробел. Идентификаторы устройств здесь не показываются." />
    <div className="quality-controls panel">
      <div className="segmented" role="group" aria-label="Период качества данных">
        {(["30d", "90d"] as const).map((value) => <button key={value} type="button" className={range === value ? "is-active" : ""} aria-pressed={range === value} onClick={() => setRange(value)}>{value === "30d" ? "30 дней" : "90 дней"}</button>)}
      </div>
      <label>Показать<select value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}><option value="all">Все метрики</option><option value="attention">Требуют внимания</option><option value="missing">С пробелами</option></select></label>
    </div>
    {quality.loading && !quality.data ? <LoadingState /> : quality.error && !quality.data ? <ErrorState message={quality.error.message} onRetry={quality.reload} /> : quality.data ? <>
      <section className="source-grid" aria-label="Состояние источников">
        {quality.data.sources.map((source) => <article className={`source-card source-card--${source.status}`} key={source.key}>
          <div><strong>{sourceLabels[source.key] ?? source.key}</strong><span>{sourceStatusLabels[source.status]}</span></div>
          <small>Последняя синхронизация: {source.lastSuccessAt ? formatDateTime(source.lastSuccessAt) : "нет"}</small>
          <small>Данные актуальны на: {source.dataAsOf ? formatDateTime(source.dataAsOf) : "неизвестно"}</small>
        </article>)}
      </section>
      <div className="quality-legend" aria-label="Легенда"><span><i className="quality-dot quality-dot--available" />Есть данные</span><span><i className="quality-dot quality-dot--confirmed_empty" />Подтверждённо пусто</span><span><i className="quality-dot quality-dot--missing" />Пробел</span></div>
      {metrics.length ? <section className="panel quality-panel" aria-labelledby="quality-matrix-title">
        <div className="panel__head"><div><span className="eyebrow">{formatDate(quality.data.from)} — {formatDate(quality.data.to)}</span><h2 id="quality-matrix-title">Покрытие по дням</h2><p>В матрице показаны завершённые календарные дни в {quality.data.timezone}.</p></div></div>
        <div className="quality-matrix-scroll" ref={scroll} tabIndex={0} aria-label="Прокручиваемая матрица качества данных">
          <table className="quality-matrix">
            <thead><tr><th scope="col">Метрика</th>{metrics[0]?.days.map((day) => <th scope="col" key={day.date} title={formatDate(day.date)}>{formatDate(day.date, false)}</th>)}</tr></thead>
            <tbody>{metrics.map((metric) => <tr key={metric.key}>
              <th scope="row"><strong>{metricLabels[metric.key] ?? metric.key}</strong><small>{policyLabel(metric)}</small><span>{formatNumber(metric.coverage.withValues, 0)} с данными · {formatNumber(metric.coverage.confirmedEmpty, 0)} пусто · {formatNumber(metric.coverage.missing, 0)} пробелов</span></th>
              {metric.days.map((day) => <td key={day.date} aria-label={`${metricLabels[metric.key] ?? metric.key}: ${dayLabel(day)}`} title={dayLabel(day)}><i className={`quality-dot quality-dot--${day.state}`} aria-hidden="true" /></td>)}
            </tr>)}</tbody>
          </table>
        </div>
      </section> : <EmptyState title="Нет метрик по выбранному фильтру" text="Измените фильтр или период, чтобы увидеть остальное покрытие." />}
      <aside className="info-note"><p><strong>Шаги:</strong> используются только завершённые данные Xiaomi Cloud. Если их нет, Amigo показывает пробел и не подставляет шаги из Health Connect или нулевое значение.</p></aside>
    </> : null}
  </>;
}
