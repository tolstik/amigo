import { useCallback, useMemo } from "react";
import { api, csvUrl } from "../api/client";
import type { PressurePoint, PressureStats } from "../api/types";
import { pressureCategoryChartOption, pressureChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { PressureTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { PressureCategoryGuide } from "../components/PressureCategoryGuide";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
import { formatDateTime, formatNumber } from "../lib/format";
import { aggregateDailyPressureCategories } from "../lib/pressureCategories";

function mean(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function variation(values: number[]): number | null {
  const average = mean(values);
  if (average === null || values.length < 2) return null;
  return Math.sqrt(values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1));
}

function computeStats(points: PressurePoint[], days: number): PressureStats {
  const cutoff = Date.now() - days * 86_400_000;
  const selected = points.filter((point) => new Date(point.measuredAt).getTime() >= cutoff);
  const systolic = selected.map((point) => point.systolic);
  const diastolic = selected.map((point) => point.diastolic);
  const pulse = selected.flatMap((point) => point.pulse === null ? [] : [point.pulse]);
  return {
    avgSystolic: mean(systolic),
    avgDiastolic: mean(diastolic),
    avgPulse: mean(pulse),
    minSystolic: systolic.length ? Math.min(...systolic) : null,
    maxSystolic: systolic.length ? Math.max(...systolic) : null,
    minDiastolic: diastolic.length ? Math.min(...diastolic) : null,
    maxDiastolic: diastolic.length ? Math.max(...diastolic) : null,
    variabilitySystolic: variation(systolic),
    variabilityDiastolic: variation(diastolic),
    sessions: selected.length,
  };
}

function pressurePair(systolic: number | null, diastolic: number | null): string {
  if (systolic === null || diastolic === null) return "—";
  return `${formatNumber(systolic, 0)} / ${formatNumber(diastolic, 0)}`;
}

function byPeriod(points: PressurePoint[], period: "morning" | "evening") {
  const selected = points.filter((point) => point.periodOfDay === period);
  return {
    systolic: mean(selected.map((point) => point.systolic)),
    diastolic: mean(selected.map((point) => point.diastolic)),
    count: selected.length,
  };
}

export function PressurePage() {
  const [period, setPeriod] = useChartPeriod("90d");
  const loadSeries = useCallback((signal: AbortSignal) => api.pressure(period, signal), [period]);
  const series = useApi(loadSeries);
  const points = series.data?.points ?? [];
  const latest = points.at(-1);
  const stats7d = useMemo(() => series.data?.stats7d.sessions ? series.data.stats7d : computeStats(points, 7), [points, series.data?.stats7d]);
  const stats30d = useMemo(() => series.data?.stats30d.sessions ? series.data.stats30d : computeStats(points, 30), [points, series.data?.stats30d]);
  const morning = useMemo(() => byPeriod(points, "morning"), [points]);
  const evening = useMemo(() => byPeriod(points, "evening"), [points]);
  const dailyCategories = useMemo(
    () => aggregateDailyPressureCategories(points, series.data?.meta.timezone || "Europe/Moscow"),
    [points, series.data?.meta.timezone],
  );

  return (
    <>
      <PageHeader
        eyebrow="Наблюдение"
        title="Статистика давления"
        description="Систолическое, диастолическое и пульс собраны по сессиям. Линии показывают измерения, а отдельная дневная полоса — заданный визуальный ориентир, не медицинскую оценку."
        actions={<a className="button button--secondary" href={csvUrl("pressure", period)} download><Icon name="download" /> Скачать CSV</a>}
      />

      <section className="kpi-grid" aria-label="Показатели давления">
        <KpiCard label="Последняя сессия" value={latest ? pressurePair(latest.systolic, latest.diastolic) : "—"} hint={latest ? formatDateTime(latest.measuredAt) : "Нет измерений"} icon="heart" tone="plain" featured />
        <KpiCard label="Среднее за 7 дней" value={pressurePair(stats7d.avgSystolic, stats7d.avgDiastolic)} hint={`${stats7d.sessions} сессий`} icon="activity" tone="blue" />
        <KpiCard label="Среднее за 30 дней" value={pressurePair(stats30d.avgSystolic, stats30d.avgDiastolic)} hint={`${stats30d.sessions} сессий`} icon="history" tone="violet" />
        <KpiCard label="Средний пульс, 30 дней" value={stats30d.avgPulse === null ? "—" : `${formatNumber(stats30d.avgPulse, 0)} уд/мин`} hint="По сессиям с пульсом" icon="activity" tone="plain" />
      </section>

      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} options={["30d", "90d", "1y", "all"]} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : points.length ? (
        <>
          <ChartCard
            title="Динамика показателей"
            subtitle={`${series.data?.meta.count ?? points.length} сессий · пунктиром отмечены границы повышенного и критически высокого давления, пульс на правой шкале`}
            option={pressureChartOption(points)}
            ariaLabel="График систолического и диастолического давления и пульса"
            height={460}
            footer={<PressureTable points={points} />}
          />
          <ChartCard
            title="Дневной визуальный ориентир"
            subtitle={`${dailyCategories.length} дней с сессиями · категория дня — наиболее требующая внимания среди сессий`}
            option={pressureCategoryChartOption(dailyCategories)}
            ariaLabel="Дневная полоса категорий давления: ниже ориентира, домашний ориентир, повышенное и критически высокое"
            height={220}
            aside={<span className="rules-badge"><Icon name="heart" /> Не диагноз</span>}
            footer={<PressureCategoryGuide days={dailyCategories} />}
          />
        </>
      ) : <EmptyState title="Измерений давления пока нет" text="После импорта данных из облака здесь появится история по сессиям." />}

      {points.length > 0 && (
        <section className="stats-layout">
          <article className="panel stats-panel">
            <div className="panel__head"><div><span className="eyebrow">Разброс значений</span><h2>7 и 30 дней</h2></div></div>
            <div className="stats-table" role="table" aria-label="Статистика давления за 7 и 30 дней">
              <div className="stats-table__row stats-table__head" role="row"><span role="columnheader">Показатель</span><span role="columnheader">7 дней</span><span role="columnheader">30 дней</span></div>
              <div className="stats-table__row" role="row"><span role="cell">Минимум, сист. / диаст.</span><strong role="cell">{pressurePair(stats7d.minSystolic, stats7d.minDiastolic)}</strong><strong role="cell">{pressurePair(stats30d.minSystolic, stats30d.minDiastolic)}</strong></div>
              <div className="stats-table__row" role="row"><span role="cell">Максимум, сист. / диаст.</span><strong role="cell">{pressurePair(stats7d.maxSystolic, stats7d.maxDiastolic)}</strong><strong role="cell">{pressurePair(stats30d.maxSystolic, stats30d.maxDiastolic)}</strong></div>
              <div className="stats-table__row" role="row"><span role="cell">Вариабельность σ</span><strong role="cell">{pressurePair(stats7d.variabilitySystolic, stats7d.variabilityDiastolic)}</strong><strong role="cell">{pressurePair(stats30d.variabilitySystolic, stats30d.variabilityDiastolic)}</strong></div>
            </div>
          </article>
          <article className="panel dayparts-panel">
            <div className="panel__head"><div><span className="eyebrow">Время суток</span><h2>Утро и вечер</h2></div></div>
            <div className="daypart"><span><Icon name="clock" /> Утренние сессии</span><strong>{pressurePair(morning.systolic, morning.diastolic)}</strong><small>{morning.count} сессий</small></div>
            <div className="daypart"><span><Icon name="clock" /> Вечерние сессии</span><strong>{pressurePair(evening.systolic, evening.diastolic)}</strong><small>{evening.count} сессий</small></div>
          </article>
        </section>
      )}

      <aside className="info-note"><Icon name="heart" /><p><strong>Это статистический дневник.</strong> Он не ставит диагнозы и не заменяет консультацию медицинского специалиста.</p></aside>
    </>
  );
}
