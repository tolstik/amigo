import { useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { LabResult, LabStatus } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate } from "../lib/format";

const statusLabel: Record<LabStatus, string> = {
  within_reference: "В референсе",
  below_reference: "Ниже референса",
  above_reference: "Выше референса",
  outside_reference: "Вне референса",
  indeterminate: "Без оценки",
};

export function labValue(row: LabResult): string {
  const value = row.value_numeric ?? row.value_text ?? "—";
  return `${row.comparator && row.comparator !== "=" ? row.comparator : ""}${value}${row.unit ? ` ${row.unit}` : ""}`;
}

export function labReference(row: LabResult): string {
  if (row.reference_text) return row.reference_text;
  if (row.reference_low !== null && row.reference_high !== null) return `${row.reference_low}–${row.reference_high} ${row.unit ?? ""}`;
  if (row.reference_low !== null) return `от ${row.reference_low} ${row.unit ?? ""}`;
  if (row.reference_high !== null) return `до ${row.reference_high} ${row.unit ?? ""}`;
  return "Не указан";
}

export function LabsPage() {
  const loader = useCallback((signal: AbortSignal) => api.labSummary(signal), []);
  const summary = useApi(loader);
  useEffect(() => {
    const timer = window.setInterval(summary.reload, 10_000);
    return () => window.clearInterval(timer);
  }, [summary.reload]);
  const rows = summary.data?.items ?? [];
  return (
    <>
      <PageHeader eyebrow="Лаборатория" title="Результаты анализов" description="Последние значения, референсы и история. Сейчас используются только диапазоны из бланка или введённые вами вручную." actions={<Link className="button button--primary" to="/labs/upload">Загрузить анализы</Link>} />
      {summary.loading && <LoadingState />}
      {summary.error && <ErrorState onRetry={summary.reload} />}
      {!summary.loading && !summary.error && !rows.length && <EmptyState title="Анализов пока нет" text="Загрузите PDF, JPG, PNG или HEIC — распознавание и извлечение пройдут в изолированном локальном контуре." />}
      {!!rows.length && <>
        <div className="lab-counts">
          <article className="panel"><span>В референсе</span><strong>{summary.data?.counts.within_reference ?? 0}</strong></article>
          <article className="panel"><span>Требуют внимания</span><strong>{(summary.data?.counts.below_reference ?? 0) + (summary.data?.counts.above_reference ?? 0) + (summary.data?.counts.outside_reference ?? 0)}</strong></article>
          <article className="panel"><span>Без оценки</span><strong>{summary.data?.counts.indeterminate ?? 0}</strong></article>
        </div>
        <section className="panel lab-table-card">
          <div className="data-table-scroll"><table className="data-table lab-table"><thead><tr><th>Показатель</th><th>Последнее значение</th><th>Референс</th><th>Статус</th><th>Дата</th><th>Проверка</th></tr></thead><tbody>
            {rows.map((row) => <tr key={row.id}><th><Link to={`/labs/analytes/${encodeURIComponent(row.analyte_id ?? "")}`}>{row.analyte_name}</Link></th><td>{labValue(row)}</td><td>{labReference(row)}<small>{row.reference_source === "laboratory" ? "Бланк" : row.reference_source === "catalog" ? "Справочник" : row.reference_source === "user" ? "Исправлено" : ""}</small></td><td><span className={`lab-status lab-status--${row.status}`}>{statusLabel[row.status]}</span></td><td>{formatDate(row.observed_on)}</td><td>{row.verification_status === "verified" ? "Проверено" : "Не проверено"}</td></tr>)}
          </tbody></table></div>
        </section>
      </>}
    </>
  );
}
