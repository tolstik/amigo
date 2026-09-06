import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { Period, SwimmingSession } from "../api/types";
import { swimmingChartOption } from "../charts/options";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
import { formatDateTime, formatNumber } from "../lib/format";

function quantity(value: number | null | undefined, unit: string, digits = 0): string {
  return value == null ? "—" : `${formatNumber(value, digits)} ${unit}`;
}

function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  return `${hours ? `${hours} ч ` : ""}${Math.floor(total % 3600 / 60)} мин ${total % 60} с`;
}

function pace(seconds: number | null): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")} / 100 м`;
}

const styles = { freestyle: "Вольный стиль", breaststroke: "Брасс", backstroke: "На спине", butterfly: "Баттерфляй", medley: "Смешанный" };

function SessionDetails({ item }: { item: SwimmingSession }) {
  const fields = [
    ["Начало", formatDateTime(item.start_time, true)],
    ["Окончание", formatDateTime(item.end_time, true)],
    ["Длительность с паузами", duration(item.duration_seconds)],
    ["Активное время", duration(item.active_duration_seconds)],
    ["Дистанция", quantity(item.distance_meters, "м")],
    ["Темп по активному времени", pace(item.pace_seconds_per_100m)],
    ["Активные калории", quantity(item.kilocalories, "ккал")],
    ["Пульс: минимум", quantity(item.minimum_bpm, "уд/мин")],
    ["Пульс: средний", quantity(item.average_bpm, "уд/мин")],
    ["Пульс: максимум", quantity(item.maximum_bpm, "уд/мин")],
    ["Дорожки", quantity(item.pool_lengths, "шт.")],
    ["Длина бассейна", quantity(item.pool_length_meters, "м", 1)],
    ["Стиль плавания", item.stroke_style ? styles[item.stroke_style] ?? "—" : "—"],
  ];
  return <details className="swimming-session">
    <summary><strong>{formatDateTime(item.start_time, true)}</strong><span>{quantity(item.distance_meters, "м")}</span><span>{duration(item.duration_seconds)}</span></summary>
    <dl className="swimming-details">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="swimming-note">Прочерк означает, что Xiaomi не передал подтверждённое значение. Темп рассчитывается по активному времени и дистанции.</p>
  </details>;
}

function SwimmingPeriod({ period }: { period: Period }) {
  const [offset, setOffset] = useState(0);
  const load = useCallback((signal: AbortSignal) => api.swimming(period, offset, signal), [period, offset]);
  const result = useApi(load);
  if (result.loading && !result.data) return <LoadingState />;
  if (result.error) return <ErrorState message={result.error.message} onRetry={result.reload} />;
  if (!result.data) return null;
  const { summary, sessions, coverage, points } = result.data;
  const partialHint = coverage.status === "partial" ? "В загруженной части периода" : "За выбранный период";
  const countHint = (count: number) => summary.workouts && count < summary.workouts
    ? `Данные у ${count} из ${summary.workouts} тренировок` : partialHint;
  return <>
    <section className="kpi-grid" aria-label="Показатели бассейна">
      <KpiCard label="Тренировки" value={coverage.status === "missing" ? "—" : formatNumber(summary.workouts, 0)} hint={partialHint} icon="activity" tone="blue" featured />
      <KpiCard label="Дистанция" value={quantity(summary.distance_meters, "м")} hint={countHint(summary.distance_meters_count)} icon="progress" tone="green" />
      <KpiCard label="Длительность" value={duration(summary.duration_seconds)} hint={countHint(summary.duration_seconds_count)} icon="clock" tone="violet" />
      <KpiCard label="Активные калории" value={quantity(summary.kilocalories, "ккал")} hint={countHint(summary.kilocalories_count)} icon="activity" tone="coral" />
    </section>
    <p className="swimming-coverage" role="status">
      {coverage.status === "missing" ? "История Xiaomi за этот период ещё не загружена." : coverage.status === "partial" ? "История загружена частично. Полные итоги появятся после завершения синхронизации." : `История проверена по ${formatDateTime(coverage.to, true)}.`}
      {coverage.data_as_of && ` Обновлено ${formatDateTime(coverage.data_as_of, true)}.`}
    </p>
    {points.length > 0 && <>
      <ChartCard title="Дистанция по тренировкам" subtitle="Каждый столбик — отдельная тренировка в бассейне; пропуски остаются пустыми" option={swimmingChartOption(points, "distance_meters")} ariaLabel="Дистанция плавания в метрах по тренировкам" height={300} />
      <ChartCard title="Длительность по тренировкам" subtitle="Полное время тренировки, включая паузы" option={swimmingChartOption(points, "duration_seconds")} ariaLabel="Длительность тренировок в бассейне в минутах" height={300} />
    </>}
    {summary.workouts ? <section className="panel swimming-history" aria-labelledby="swimming-history-title" aria-busy={result.loading}>
      <div className="panel__head"><div><h2 id="swimming-history-title">История тренировок</h2><p>Откройте тренировку, чтобы посмотреть подробности · время московское</p></div></div>
      {sessions.map((item) => <SessionDetails key={item.id} item={item} />)}
      <div className="swimming-pagination">
        <span>{offset + 1}–{offset + sessions.length} из {summary.workouts}</span>
        <button className="button button--secondary" disabled={result.loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Более новые</button>
        <button className="button button--secondary" disabled={result.loading || result.data.next_offset === null} onClick={() => setOffset(result.data!.next_offset!)}>Более ранние</button>
      </div>
    </section> : <EmptyState title={coverage.status === "confirmed_empty" ? "Тренировок в бассейне за этот период нет" : "Тренировок в бассейне пока нет"} text={coverage.status === "confirmed_empty" ? "Xiaomi подтвердил спортивную историю за выбранный период." : "Обновите Amigo до версии 1.5.0 и выполните синхронизацию Xiaomi. Ранее записанные тренировки будут загружаться постепенно."} />}
  </>;
}

export function SwimmingPage() {
  const [period, setPeriod] = useChartPeriod("90d");
  return <>
    <PageHeader eyebrow="Xiaomi Cloud" title="Бассейн" description="Тренировки по плаванию в бассейне: дистанция, время и подробности с часов." />
    <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
    <SwimmingPeriod key={period} period={period} />
  </>;
}
