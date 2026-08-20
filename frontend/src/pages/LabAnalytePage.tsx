import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate } from "../lib/format";
import type { LabResult } from "../api/types";
import { labHistoryChartOption } from "../charts/options";
import { ChartCard } from "../components/ChartCard";
import { labReference, labValue } from "./LabsPage";

function HistoryChart({ unit, rows }: { unit: string; rows: LabResult[] }) {
  const numeric = rows.filter((row) => row.value_numeric !== null);
  return <ChartCard
    title={unit}
    subtitle={`${numeric.length} числовых значений; референсы показаны отдельными линиями`}
    option={labHistoryChartOption(numeric, unit)}
    ariaLabel={`Динамика показателя в единицах ${unit}`}
    height={340}
  />;
}

export function LabAnalytePage() {
  const { id = "" } = useParams();
  const loader = useCallback((signal: AbortSignal) => api.labHistory(id, signal), [id]);
  const history = useApi(loader);
  const rows = history.data?.items ?? [];
  const numericByUnit = rows.reduce<Record<string, LabResult[]>>((groups, row) => {
    if (row.value_numeric === null) return groups;
    const unit = row.unit?.trim() || "Без единицы";
    (groups[unit] ??= []).push(row);
    return groups;
  }, {});
  return <>
    <PageHeader eyebrow="История показателя" title={rows.at(-1)?.analyte_name ?? "Лабораторный показатель"} description="Несовместимые единицы показаны как отдельные значения и автоматически не конвертируются." actions={<Link className="button button--secondary" to="/labs">К анализам</Link>} />
    {history.loading && <LoadingState />}
    {history.error && <ErrorState onRetry={history.reload} />}
    {!history.loading && !rows.length && <EmptyState title="Истории пока нет" text="Для графика нужна хотя бы одна строка с датой и значением." />}
    {!!Object.keys(numericByUnit).length && <div className="lab-history-charts">{Object.entries(numericByUnit).map(([unit, unitRows]) => <HistoryChart key={unit} unit={unit} rows={unitRows} />)}</div>}
    {!!rows.length && <section className="panel lab-table-card"><div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Значение</th><th>Референс</th><th>Источник</th><th>Проверка</th></tr></thead><tbody>{[...rows].reverse().map((row) => <tr key={row.id}><td>{formatDate(row.observed_on)}</td><td>{labValue(row)}</td><td>{labReference(row)}</td><td><Link to={`/labs/documents/${row.document_id}`}>Документ</Link></td><td>{row.verification_status === "verified" ? "Проверено" : "Не проверено"}</td></tr>)}</tbody></table></div></section>}
  </>;
}
