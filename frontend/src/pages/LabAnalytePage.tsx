import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate } from "../lib/format";
import type { LabResult } from "../api/types";
import { labReference, labValue } from "./LabsPage";

function HistoryChart({ unit, rows }: { unit: string; rows: LabResult[] }) {
  const numeric = rows.filter((row) => row.value_numeric !== null);
  const scaleValues = numeric.flatMap((row) => [
    row.value_numeric!,
    ...(row.reference_low !== null ? [row.reference_low] : []),
    ...(row.reference_high !== null ? [row.reference_high] : []),
  ]);
  const min = Math.min(...scaleValues);
  const max = Math.max(...scaleValues);
  const span = max > min ? max - min : 1;
  const x = (index: number) => numeric.length === 1 ? 50 : (index / (numeric.length - 1)) * 100;
  const y = (value: number) => 90 - ((value - min) / span) * 80;
  const points = numeric.map((row, index) => `${x(index)},${y(row.value_numeric!)}`).join(" ");

  return <section className="panel lab-history-chart">
    <div className="panel__head"><div><h2>{unit}</h2><p>{numeric.length} числовых значений</p></div></div>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={`Динамика показателя в единицах ${unit}`}>
      <line x1="0" y1="90" x2="100" y2="90" />
      {numeric.map((row, index) => {
        if (row.reference_low === null || row.reference_high === null) return null;
        const center = x(index);
        const left = index === 0 ? 0 : (x(index - 1) + center) / 2;
        const right = index === numeric.length - 1 ? 100 : (center + x(index + 1)) / 2;
        const top = y(Math.max(row.reference_low, row.reference_high));
        const bottom = y(Math.min(row.reference_low, row.reference_high));
        return <rect key={`range-${row.id}`} className="lab-reference-band" x={left} y={top} width={right - left} height={bottom - top} />;
      })}
      <polyline points={points} />
      {numeric.map((row, index) => <circle key={row.id} className="lab-history-point" cx={x(index)} cy={y(row.value_numeric!)} r="1.7" />)}
    </svg>
    <div className="lab-history-chart__axis"><span>{formatDate(numeric[0].observed_on)}</span><span>{formatDate(numeric.at(-1)?.observed_on)}</span></div>
  </section>;
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
