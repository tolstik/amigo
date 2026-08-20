import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, studyEventsUrl } from "../api/client";
import type { StudyDocument, StudyModality } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";

export function StudyDocumentPage() {
  const { id = "" } = useParams();
  const loader = useCallback((signal: AbortSignal) => api.study(id, signal), [id]);
  const study = useApi(loader);
  const [form, setForm] = useState<StudyDocument | null>(null);
  useEffect(() => { if (study.data) setForm(study.data); }, [study.data]);
  useEffect(() => {
    const events = new EventSource(studyEventsUrl(), { withCredentials: true });
    events.addEventListener("queue", study.reload);
    return () => events.close();
  }, [study.reload]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    await api.patchStudy(id, {
      modality: form.modality,
      title: form.title,
      observed_on: form.observed_on,
      findings: form.findings,
      conclusion: form.conclusion,
    });
    study.reload();
  }

  async function confirm() { await api.confirmStudy(id); study.reload(); }
  async function retry() { await api.retryStudy(id); study.reload(); }
  return <>
    <PageHeader eyebrow="Исследование" title={form?.title || form?.filename || "Заключение"} description="Сверьте выделенные факты и заключение с оригиналом перед подтверждением." actions={<><Link className="button button--secondary" to="/studies">К списку</Link><Link className="button button--secondary" to={`/studies/${id}/view`}>Посмотреть оригинал</Link>{form?.status === "complete" && !form.verified && <button className="button button--primary" onClick={confirm}>Всё проверено</button>}{form?.status === "failed" && <button className="button button--primary" onClick={retry}>Повторить</button>}</>} />
    {study.loading && !study.data && <LoadingState />}
    {study.error && <ErrorState onRetry={study.reload} />}
    {form && form.status !== "complete" && <div className="panel processing-card"><span className="spinner" /><div><strong>Исследование обрабатывается</strong><p>{form.error_code || `Этап: ${form.processing_stage}, ${form.progress_percent}%`}</p></div></div>}
    {form?.status === "complete" && <form className="panel study-editor" onSubmit={save}>
      <label>Название<input value={form.title ?? ""} onChange={(event) => setForm({ ...form, title: event.target.value || null })} /></label>
      <label>Тип<select value={form.modality} onChange={(event) => setForm({ ...form, modality: event.target.value as StudyModality })}><option value="ultrasound">УЗИ</option><option value="mri">МРТ</option><option value="ct">КТ</option><option value="xray">Рентген</option><option value="ecg">ЭКГ</option><option value="other">Другое</option></select></label>
      <label>Дата<input type="date" value={form.observed_on ?? ""} onChange={(event) => setForm({ ...form, observed_on: event.target.value || null })} /></label>
      <label className="study-editor__wide">Наблюдения, по одному абзацу<textarea rows={10} value={form.findings.join("\n\n")} onChange={(event) => setForm({ ...form, findings: event.target.value.split(/\n\s*\n/).map((value) => value.trim()).filter(Boolean) })} /></label>
      <label className="study-editor__wide">Заключение<textarea rows={7} value={form.conclusion ?? ""} onChange={(event) => setForm({ ...form, conclusion: event.target.value || null })} /></label>
      <button className="button button--primary">Сохранить исправления</button>
    </form>}
    {form?.extracted_text && <details className="panel extracted-text"><summary>Распознанный текст для сверки</summary><pre>{form.extracted_text}</pre></details>}
  </>;
}
