import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, labDownloadUrl, labEventsUrl } from "../api/client";
import type { LabResult, LabResultInput } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { labReference, labValue } from "./LabsPage";

function ResultEditor({ row, onSaved }: { row: LabResult; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(row);
  async function save(event: FormEvent) {
    event.preventDefault();
    await api.patchLabResult(row.id, {
      analyte_name: form.analyte_name,
      value_numeric: form.value_numeric,
      value_text: form.value_text,
      comparator: form.comparator,
      unit: form.unit,
      observed_on: form.observed_on,
      specimen: form.specimen,
      method: form.method,
      reference_low: form.reference_low,
      reference_high: form.reference_high,
      reference_text: form.reference_text,
      deleted: form.deleted,
    });
    setEditing(false);
    onSaved();
  }
  if (!editing) return <tr className={row.deleted ? "is-deleted" : ""}><th>{row.analyte_name}</th><td>{labValue(row)}</td><td>{labReference(row)}</td><td>{row.observed_on ?? "—"}</td><td>{row.source_page ?? "—"}</td><td><button className="button button--ghost" onClick={() => setEditing(true)}>Исправить</button></td></tr>;
  return <tr className="result-edit-row"><td colSpan={6}><form className="inline-editor" onSubmit={save}>
    <label>Показатель<input required value={form.analyte_name} onChange={(event) => setForm({ ...form, analyte_name: event.target.value })} /></label>
    <label>Число<input type="number" step="any" value={form.value_numeric ?? ""} onChange={(event) => setForm({ ...form, value_numeric: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Текст<input value={form.value_text ?? ""} onChange={(event) => setForm({ ...form, value_text: event.target.value || null })} /></label>
    <label>Сравнение<select value={form.comparator ?? ""} onChange={(event) => setForm({ ...form, comparator: (event.target.value || null) as LabResult["comparator"] })}><option value="">Нет</option><option value="<">&lt;</option><option value="<=">≤</option><option value="=">=</option><option value=">=">≥</option><option value=">">&gt;</option></select></label>
    <label>Единица<input value={form.unit ?? ""} onChange={(event) => setForm({ ...form, unit: event.target.value || null })} /></label>
    <label>Дата<input type="date" value={form.observed_on ?? ""} onChange={(event) => setForm({ ...form, observed_on: event.target.value || null })} /></label>
    <label>Материал<input value={form.specimen ?? ""} onChange={(event) => setForm({ ...form, specimen: event.target.value || null })} /></label>
    <label>Метод<input value={form.method ?? ""} onChange={(event) => setForm({ ...form, method: event.target.value || null })} /></label>
    <label>Референс от<input type="number" step="any" value={form.reference_low ?? ""} onChange={(event) => setForm({ ...form, reference_low: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Референс до<input type="number" step="any" value={form.reference_high ?? ""} onChange={(event) => setForm({ ...form, reference_high: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Референс текстом<input value={form.reference_text ?? ""} onChange={(event) => setForm({ ...form, reference_text: event.target.value || null })} /></label>
    <label className="check-inline"><input type="checkbox" checked={form.deleted} onChange={(event) => setForm({ ...form, deleted: event.target.checked })} />Не учитывать строку</label>
    <div className="inline-editor__actions"><button className="button button--primary">Сохранить</button><button type="button" className="button button--ghost" onClick={() => { setForm(row); setEditing(false); }}>Отмена</button></div>
  </form></td></tr>;
}

function emptyResult(): LabResultInput {
  return {
    analyte_name: "",
    value_numeric: null,
    value_text: null,
    comparator: null,
    unit: null,
    observed_on: null,
    specimen: null,
    method: null,
    reference_low: null,
    reference_high: null,
    reference_text: null,
    source_page: null,
  };
}

function AddResultForm({ documentId, onSaved }: { documentId: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<LabResultInput>(emptyResult);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (form.value_numeric === null && !form.value_text?.trim()) {
      setError("Укажите числовое или текстовое значение.");
      return;
    }
    setError(null);
    await api.createLabResult(documentId, form);
    setForm(emptyResult());
    setOpen(false);
    onSaved();
  }

  if (!open) {
    return <button className="button button--secondary" onClick={() => setOpen(true)}>Добавить показатель</button>;
  }
  return <form className="panel inline-editor add-result-form" onSubmit={save}>
    <div className="panel__head"><div><h2>Добавить показатель</h2><p>Для строки, которую распознавание пропустило.</p></div></div>
    <label>Показатель<input required value={form.analyte_name} onChange={(event) => setForm({ ...form, analyte_name: event.target.value })} /></label>
    <label>Число<input type="number" step="any" value={form.value_numeric ?? ""} onChange={(event) => setForm({ ...form, value_numeric: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Текст<input value={form.value_text ?? ""} onChange={(event) => setForm({ ...form, value_text: event.target.value || null })} /></label>
    <label>Сравнение<select value={form.comparator ?? ""} onChange={(event) => setForm({ ...form, comparator: (event.target.value || null) as LabResultInput["comparator"] })}><option value="">Нет</option><option value="<">&lt;</option><option value="<=">≤</option><option value="=">=</option><option value=">=">≥</option><option value=">">&gt;</option></select></label>
    <label>Единица<input value={form.unit ?? ""} onChange={(event) => setForm({ ...form, unit: event.target.value || null })} /></label>
    <label>Дата<input type="date" value={form.observed_on ?? ""} onChange={(event) => setForm({ ...form, observed_on: event.target.value || null })} /></label>
    <label>Материал<input value={form.specimen ?? ""} onChange={(event) => setForm({ ...form, specimen: event.target.value || null })} /></label>
    <label>Метод<input value={form.method ?? ""} onChange={(event) => setForm({ ...form, method: event.target.value || null })} /></label>
    <label>Референс от<input type="number" step="any" value={form.reference_low ?? ""} onChange={(event) => setForm({ ...form, reference_low: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Референс до<input type="number" step="any" value={form.reference_high ?? ""} onChange={(event) => setForm({ ...form, reference_high: event.target.value ? Number(event.target.value) : null })} /></label>
    <label>Референс текстом<input value={form.reference_text ?? ""} onChange={(event) => setForm({ ...form, reference_text: event.target.value || null })} /></label>
    {error && <p className="form-error">{error}</p>}
    <div className="inline-editor__actions"><button className="button button--primary">Сохранить</button><button type="button" className="button button--ghost" onClick={() => { setForm(emptyResult()); setError(null); setOpen(false); }}>Отмена</button></div>
  </form>;
}

export function LabDocumentPage() {
  const { id = "" } = useParams();
  const loader = useCallback((signal: AbortSignal) => api.labDocument(id, signal), [id]);
  const document = useApi(loader);
  useEffect(() => {
    const events = new EventSource(labEventsUrl(), { withCredentials: true });
    events.addEventListener("queue", document.reload);
    return () => events.close();
  }, [document.reload]);
  const row = document.data;
  async function confirm() { await api.confirmLab(id); document.reload(); }
  async function retry() { await api.retryLab(id); document.reload(); }
  return <>
    <PageHeader eyebrow="Документ" title={row?.filename ?? "Результаты распознавания"} description="Сверьте каждую строку с оригиналом. Исправленные данные сразу перестраивают историю и рекомендации." actions={<><Link className="button button--secondary" to="/labs/upload">К списку</Link>{row && <Link className="button button--secondary" to={`/labs/documents/${row.id}/view`}>Посмотреть</Link>}{row && <a className="button button--ghost" href={labDownloadUrl(row.id)}>Скачать</a>}{row?.status === "complete" && !row.verified && <button className="button button--primary" onClick={confirm}>Всё проверено</button>}{row?.status === "failed" && <button className="button button--primary" onClick={retry}>Повторить</button>}</>} />
    {document.loading && <LoadingState />}
    {document.error && <ErrorState onRetry={document.reload} />}
    {row && <>
      {row.status !== "complete" && <div className="panel processing-card"><span className="spinner" /><div><strong>{row.status === "failed" ? "Обработка не завершилась" : "Документ обрабатывается"}</strong><p>{row.error_code ?? "OCR и структурированное извлечение выполняются асинхронно."}</p></div></div>}
      {!!row.results?.length && <section className="panel lab-table-card"><div className="panel__head"><div><h2>Извлечённые результаты</h2><p>{row.verified ? "Документ подтверждён" : "Все строки пока помечены как не проверенные"}</p></div></div><div className="data-table-scroll"><table className="data-table"><thead><tr><th>Показатель</th><th>Значение</th><th>Референс</th><th>Дата</th><th>Страница</th><th /></tr></thead><tbody>{row.results.map((result) => <ResultEditor key={result.id} row={result} onSaved={document.reload} />)}</tbody></table></div></section>}
      {row.status === "complete" && <AddResultForm documentId={row.id} onSaved={document.reload} />}
      {row.extracted_text && <details className="panel extracted-text"><summary>Извлечённый текст</summary><pre>{row.extracted_text}</pre></details>}
    </>}
  </>;
}
