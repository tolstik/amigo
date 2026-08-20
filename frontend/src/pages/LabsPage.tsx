import { useCallback, useMemo, useState } from "react";
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
  const rows = summary.data?.items ?? [];
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "attention" | "unverified" | "verified">("all");
  const visibleRows = useMemo(() => {
    const wanted = query.trim().toLocaleLowerCase("ru-RU");
    return rows.filter((row) => {
      const matchesQuery = !wanted || [row.analyte_name, row.unit, row.value_text, row.observed_on]
        .some((value) => String(value ?? "").toLocaleLowerCase("ru-RU").includes(wanted));
      const matchesFilter = filter === "all"
        || (filter === "attention" && ["below_reference", "above_reference", "outside_reference"].includes(row.status))
        || (filter === "verified" && row.verification_status === "verified")
        || (filter === "unverified" && row.verification_status !== "verified");
      return matchesQuery && matchesFilter;
    });
  }, [filter, query, rows]);
  return (
    <>
      <PageHeader eyebrow="Лаборатория" title="Результаты анализов" description="Последние значения, референсы и история. Сейчас используются только диапазоны из бланка или введённые вами вручную." actions={<Link className="button button--primary" to="/labs/upload">Загрузить анализы</Link>} />
      {summary.loading && <LoadingState />}
      {summary.error && <ErrorState onRetry={summary.reload} />}
      {!summary.loading && !summary.error && !rows.length && <EmptyState title="Анализов пока нет" text="Загрузите PDF, JPG, PNG или HEIC — распознавание и извлечение пройдут в изолированном локальном контуре." />}
      {!!rows.length && <>
        <section className="panel lab-filters" aria-label="Поиск и фильтры"><label>Поиск<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Показатель, единица или дата" /></label><label>Фильтр<select value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}><option value="all">Все результаты</option><option value="attention">Требуют внимания</option><option value="unverified">Не проверены</option><option value="verified">Проверены</option></select></label><span>Найдено: {visibleRows.length}</span></section>
        <div className="lab-counts">
          <article className="panel"><span>В референсе</span><strong>{summary.data?.counts.within_reference ?? 0}</strong></article>
          <article className="panel"><span>Требуют внимания</span><strong>{(summary.data?.counts.below_reference ?? 0) + (summary.data?.counts.above_reference ?? 0) + (summary.data?.counts.outside_reference ?? 0)}</strong></article>
          <article className="panel"><span>Без оценки</span><strong>{summary.data?.counts.indeterminate ?? 0}</strong></article>
        </div>
        <section className="panel lab-table-card">
          <div className="data-table-scroll"><table className="data-table lab-table"><thead><tr><th>Показатель</th><th>Последнее значение</th><th>Референс</th><th>Статус</th><th>Дата</th><th>Проверка</th></tr></thead><tbody>
            {visibleRows.map((row) => <tr key={row.id}><th><Link to={`/labs/analytes/${encodeURIComponent(row.analyte_id ?? "")}`}>{row.analyte_name}</Link></th><td>{labValue(row)}</td><td>{labReference(row)}<small>{row.reference_source === "laboratory" ? "Бланк" : row.reference_source === "catalog" ? "Справочник" : row.reference_source === "user" ? "Исправлено" : ""}</small></td><td><span className={`lab-status lab-status--${row.status}`}>{statusLabel[row.status]}</span></td><td>{formatDate(row.observed_on)}</td><td>{row.verification_status === "verified" ? "Проверено" : "Не проверено"}</td></tr>)}
          </tbody></table></div>
        </section>
      </>}
    </>
  );
}
