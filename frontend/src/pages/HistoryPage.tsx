import { useCallback, useState } from "react";
import { api, csvUrl } from "../api/client";
import type { Period } from "../api/types";
import { weightChartOption } from "../charts/options";
import { ErrorState, EmptyState, LoadingState } from "../components/AsyncState";
import { ChartCard } from "../components/ChartCard";
import { WeightTable } from "../components/DataTables";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { formatDate, periodLabels } from "../lib/format";

export function HistoryPage() {
  const [period, setPeriod] = useState<Period>("all");
  const loadSeries = useCallback((signal: AbortSignal) => api.weight(period, signal), [period]);
  const series = useApi(loadSeries);

  return (
    <>
      <PageHeader
        eyebrow="Архив измерений"
        title="Вся история веса"
        description="Полная летопись дневных медиан, включая данные до начала текущей программы. Разрывы длиннее 14 дней не соединяются линией."
        actions={<a className="button button--secondary" href={csvUrl("weight", period)} download><Icon name="download" /> Скачать CSV</a>}
      />
      <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
      {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? (
        <ErrorState message={series.error.message} onRetry={series.reload} />
      ) : series.data?.points.length ? (
        <ChartCard
          title={periodLabels[period]}
          subtitle={`${series.data.meta.count} дней с замерами · ${formatDate(series.data.meta.from)} — ${formatDate(series.data.meta.to)}`}
          option={weightChartOption(series.data.points, false, series.data.projection, series.data.planProjection)}
          ariaLabel={`График истории веса за период ${periodLabels[period]}`}
          height={480}
          footer={<WeightTable points={series.data.points} />}
        />
      ) : <EmptyState title="В выбранном периоде нет веса" text="Выберите другой интервал или дождитесь синхронизации с облаком." />}
    </>
  );
}
