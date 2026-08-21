import { useCallback } from "react";
import { useOutletContext } from "react-router-dom";
import type { OverviewContext } from "../App";
import { api, csvUrl } from "../api/client";
import { compositionChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { CompositionTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { KpiCard } from "../components/KpiCard";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
import { formatDateTime, formatKg, formatNumber } from "../lib/format";

export function CompositionPage() {
  const overview = useOutletContext<OverviewContext>();
  const [period, setPeriod] = useChartPeriod("1y");
  const loadSeries = useCallback((signal: AbortSignal) => api.composition(period, signal), [period]);
  const series = useApi(loadSeries);
  const summary = overview.data?.composition;
  const latest = series.data?.points.at(-1);
  const fatPct = latest?.fatPct ?? summary?.fatPct;
  const fatMass = latest?.fatMassKg ?? summary?.fatMassKg;
  const leanMass = latest?.leanMassKg ?? summary?.leanMassKg;
  const latestAt = latest?.measuredAt ?? summary?.measuredAt;

  return (
    <>
      <PageHeader
        eyebrow="Состав тела"
        title="Изменения состава тела"
        description="Доля жира, жировая и безжировая масса помогают видеть изменения шире, чем одна цифра веса."
        actions={<a className="button button--secondary" href={csvUrl("composition", period)} download><Icon name="download" /> Скачать CSV</a>}
      />

      <aside className="bia-note"><span><Icon name="sparkle" /></span><p><strong>Приблизительные BIA-оценки.</strong> На них влияют вода, время суток и условия измерения. Смотрите прежде всего на долгосрочный тренд.</p></aside>

      <section className="kpi-grid kpi-grid--three" aria-label="Последний состав тела">
        <KpiCard label="Доля жира" value={fatPct == null ? "—" : `${formatNumber(fatPct)}%`} hint={latestAt ? `Замер ${formatDateTime(latestAt)}` : "Нет данных"} icon="composition" tone="violet" featured />
        <KpiCard label="Жировая масса" value={formatKg(fatMass)} hint="Приблизительная BIA-оценка" icon="scale" tone="coral" />
        <KpiCard label="Безжировая масса" value={formatKg(leanMass)} hint="Приблизительная BIA-оценка" icon="activity" tone="green" />
      </section>

      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} options={["30d", "90d", "1y", "all"]} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : series.data?.points.length ? (
        <ChartCard
          title="Тренды состава тела"
          subtitle={`${series.data.meta.count} замеров · проценты на правой шкале`}
          option={compositionChartOption(series.data.points)}
          ariaLabel="График доли жира, жировой и безжировой массы"
          height={470}
          footer={<CompositionTable points={series.data.points} />}
        />
      ) : <EmptyState title="Оценок состава тела пока нет" text="Отсутствующие показатели не восстанавливаются искусственно — график появится после реальных замеров." />}
    </>
  );
}
