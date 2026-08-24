import { FormEvent, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { LabCompareResponse, LabCompareRow, LabDocument, LabResult } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, formatNumber } from "../lib/format";

type CompareFilter = "all" | "changed" | "status_changed" | "missing" | "incompatible";

const statusLabels: Record<LabResult["status"], string> = {
  within_reference: "В референсе",
  below_reference: "Ниже референса",
  above_reference: "Выше референса",
  outside_reference: "Вне референса",
  indeterminate: "Статус не определён",
};

const incompatibilityLabels: Record<Exclude<LabCompareRow["incompatibility"], null>, string> = {
  missing_result: "Показатель есть не во всех панелях",
  multiple_results: "В одной панели несколько значений",
  non_numeric_value: "Есть текстовое значение",
  qualified_value: "Есть квалификатор < или >",
  different_unit: "Разные единицы",
  different_specimen: "Разный биоматериал",
  different_method: "Разные методы",
};

function documentLabel(document: LabDocument): string {
  const state = document.status === "complete" ? "" : " · ещё обрабатывается";
  return `${document.filename} · ${formatDate(document.completed_at ?? document.created_at)}${state}`;
}

function resultValue(result: LabResult): string {
  const value = result.value_numeric !== null ? formatNumber(result.value_numeric, 2) : result.value_text ?? "—";
  return `${result.comparator && result.comparator !== "=" ? result.comparator : ""}${value}${result.unit ? ` ${result.unit}` : ""}`;
}

function resultMeta(result: LabResult): string {
  return [result.specimen, result.method].filter(Boolean).join(" · ") || "Метод и материал не указаны";
}

function rowMatches(row: LabCompareRow, filter: CompareFilter): boolean {
  if (filter === "changed") return row.valueChanged;
  if (filter === "status_changed") return row.statusChanged;
  if (filter === "missing") return row.missing;
  if (filter === "incompatible") return !row.comparable;
  return true;
}

export function LabsComparePage() {
  const loadDocuments = useCallback((signal: AbortSignal) => api.labDocuments(signal), []);
  const documents = useApi(loadDocuments);
  const [selected, setSelected] = useState<[string, string, string]>(["", "", ""]);
  const [third, setThird] = useState(false);
  const [result, setResult] = useState<LabCompareResponse | null>(null);
  const [filter, setFilter] = useState<CompareFilter>("all");
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const completeDocuments = useMemo(() => (documents.data ?? []).filter((item) => item.status === "complete"), [documents.data]);
  const ids = selected.slice(0, third ? 3 : 2).filter(Boolean);
  const canCompare = ids.length === (third ? 3 : 2) && new Set(ids).size === ids.length;
  const visibleRows = (result?.rows ?? []).filter((row) => rowMatches(row, filter));

  function choose(index: number, value: string) {
    setSelected((current) => { const next = [...current] as [string, string, string]; next[index] = value; return next; });
    setResult(null);
    setError(null);
  }

  async function compare(event: FormEvent) {
    event.preventDefault();
    if (!canCompare) return;
    setComparing(true);
    setError(null);
    try {
      setResult(await api.compareLabs(ids));
      setFilter("all");
    } catch {
      setError("Не удалось сравнить панели. Убедитесь, что документы полностью обработаны, и попробуйте ещё раз.");
    } finally {
      setComparing(false);
    }
  }

  return <>
    <PageHeader eyebrow="Динамика лабораторных данных" title="Сравнение анализов" description="Выберите две или три панели. Показатели сопоставляются только по сохранённому каноническому идентификатору — без догадок по похожим названиям и без преобразования единиц." actions={<Link className="button button--secondary" to="/labs">Архив анализов</Link>} />
    {documents.loading && !documents.data ? <LoadingState /> : documents.error && !documents.data ? <ErrorState message={documents.error.message} onRetry={documents.reload} /> : <>
      <form className="compare-picker panel" onSubmit={compare}>
        <div className="compare-picker__slots">
          {[0, 1, ...(third ? [2] : [])].map((index) => <label key={index}><span>{index === 0 ? "Базовая панель" : index === 1 ? "Сравнение" : "Третья панель"}</span><select aria-label={index === 0 ? "Базовая панель" : index === 1 ? "Сравниваемая панель" : "Третья панель"} value={selected[index]} onChange={(event) => choose(index, event.target.value)} required><option value="">Выберите документ</option>{completeDocuments.map((document) => <option key={document.id} value={document.id} disabled={selected.some((value, selectedIndex) => selectedIndex !== index && value === document.id)}>{documentLabel(document)}</option>)}</select></label>)}
        </div>
        <div className="compare-picker__actions">{!third ? <button className="button button--ghost" type="button" onClick={() => setThird(true)}>Добавить третью панель</button> : <button className="button button--ghost" type="button" onClick={() => { setThird(false); choose(2, ""); }}>Убрать третью панель</button>}<button className="button button--primary" disabled={!canCompare || comparing}>{comparing ? "Сравниваем…" : "Сравнить"}</button></div>
        {completeDocuments.length < 2 && <p className="form-error">Для сравнения нужны минимум два полностью обработанных лабораторных документа.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
      {result && <section className="panel compare-results" aria-labelledby="lab-compare-title">
        <div className="panel__head"><div><span className="eyebrow">{result.panels.length} панели</span><h2 id="lab-compare-title">Сопоставленные показатели</h2><p>Несовместимые и повторяющиеся значения остаются видимыми, но числовая дельта для них не рассчитывается.</p></div><label className="compare-filter">Фильтр<select value={filter} onChange={(event) => setFilter(event.target.value as CompareFilter)}><option value="all">Все</option><option value="changed">Значение изменилось</option><option value="status_changed">Статус изменился</option><option value="missing">Есть пропуск</option><option value="incompatible">Несовместимые</option></select></label></div>
        {visibleRows.length ? <div className="lab-compare-scroll" tabIndex={0}>
          <table className="lab-compare-table">
            <thead><tr><th scope="col">Показатель</th>{result.panels.map((panel, index) => <th scope="col" key={panel.documentId}>{index === 0 ? "База" : `Панель ${index + 1}`}<small>{panel.observedOn ? formatDate(panel.observedOn) : "Дата не указана"} · {panel.verified ? "подтверждена" : "не подтверждена"}</small></th>)}<th scope="col">Изменение от базы</th></tr></thead>
            <tbody>{visibleRows.map((row, rowIndex) => <tr key={`${row.analyteId ?? "unmatched"}-${rowIndex}`}>
              <th scope="row"><strong>{row.analyteName}</strong>{row.analyteId ? <small>Канонически сопоставлен</small> : <small>Не сопоставлен</small>}{row.incompatibility && <em>{incompatibilityLabels[row.incompatibility]}</em>}</th>
              {result.panels.map((panel, panelIndex) => <td key={panel.documentId}>{row.cells[panelIndex]?.length ? row.cells[panelIndex].map((item) => <div className="compare-value" key={item.id}><strong>{resultValue(item)}</strong><span>{statusLabels[item.status]}</span><small>{resultMeta(item)}</small></div>) : <span className="missing-value">Нет результата</span>}</td>)}
              <td>{row.deltas.length ? row.deltas.map((delta) => <div className="compare-delta" key={delta.toDocumentId}><strong>{delta.absolute > 0 ? "+" : ""}{formatNumber(delta.absolute, 2)}</strong><small>{delta.percent === null ? "процент не рассчитан" : `${delta.percent > 0 ? "+" : ""}${formatNumber(delta.percent, 1)}%`}</small></div>) : <span className="missing-value">Не рассчитывается</span>}</td>
            </tr>)}</tbody>
          </table>
        </div> : <p className="compare-empty">По выбранному фильтру строк нет.</p>}
      </section>}
    </>}
  </>;
}
