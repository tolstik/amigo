import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, studyEventsUrl } from "../api/client";
import type { StudyDocument, StudyModality } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, formatDateTime } from "../lib/format";

const modalityLabel: Record<StudyModality, string> = {
  ultrasound: "УЗИ",
  mri: "МРТ",
  ct: "КТ",
  xray: "Рентген",
  ecg: "ЭКГ",
  other: "Другое",
};

export function StudiesPage() {
  const loader = useCallback((signal: AbortSignal) => api.studies(signal), []);
  const studies = useApi(loader);
  const [modality, setModality] = useState<StudyModality>("ultrasound");
  const [observedOn, setObservedOn] = useState("");
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const events = new EventSource(studyEventsUrl(), { withCredentials: true });
    events.addEventListener("queue", studies.reload);
    return () => events.close();
  }, [studies.reload]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []).slice(0, 25);
    event.target.value = "";
    if (!files.length) return;
    setUploading(true);
    setMessage(null);
    let accepted = 0;
    try {
      for (const file of files) {
        if (file.size > 20 * 1024 * 1024) continue;
        try {
          await api.uploadStudy(file, modality, undefined, observedOn || undefined);
          accepted += 1;
        } catch {
          // Continue with the remaining reports; the final count makes partial acceptance visible.
        }
      }
    } finally {
      setUploading(false);
    }
    setMessage(`${accepted} из ${files.length} исследований поставлены в очередь.`);
    studies.reload();
  }

  async function remove(row: StudyDocument) {
    if (!window.confirm(`Удалить исследование «${row.title || row.filename}»?`)) return;
    await api.deleteStudy(row.id);
    studies.reload();
  }

  const rows = studies.data ?? [];
  return <>
    <PageHeader eyebrow="Документы исследований" title="Исследования" description="Заключения УЗИ, МРТ, КТ, рентгена и других исследований. DICOM в этой версии не поддерживается." />
    <section className="panel study-upload">
      <label>Тип исследования<select value={modality} onChange={(event) => setModality(event.target.value as StudyModality)}>{Object.entries(modalityLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Дата исследования<input type="date" value={observedOn} onChange={(event) => setObservedOn(event.target.value)} /></label>
      <label className={`button button--primary${uploading ? " is-disabled" : ""}`}>{uploading ? "Загружаем…" : "Выбрать до 25 файлов"}<input className="visually-hidden" type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.heic,.heif,application/pdf,image/jpeg,image/png,image/heic" disabled={uploading} onChange={upload} /></label>
    </section>
    {message && <p className="form-status">{message}</p>}
    {studies.loading && !studies.data && <LoadingState />}
    {studies.error && <ErrorState onRetry={studies.reload} />}
    {!studies.loading && !studies.error && !rows.length && <EmptyState title="Исследований пока нет" text="Загрузите отчёт или заключение в PDF, JPG, PNG или HEIC." />}
    {!!rows.length && <section className="document-list">{rows.map((row) => <article className="panel document-row" key={row.id}>
      <div><strong>{row.title || row.filename}</strong><small>{modalityLabel[row.modality]} · {formatDate(row.observed_on)} · загружено {formatDateTime(row.created_at)}</small>{row.queue_position && <small>Позиция в очереди: {row.queue_position}</small>}<div className="queue-progress"><span style={{ width: `${row.progress_percent}%` }} /></div></div>
      <span className={`job-status job-status--${row.status}`}>{row.status === "complete" ? row.verified ? "Проверено" : "Нужно проверить" : row.status === "queued" ? "В очереди" : row.status === "processing" ? "Обрабатывается" : "Ошибка"}</span>
      <div className="document-row__actions"><Link className="button button--secondary" to={`/studies/${row.id}/view`}>Посмотреть</Link><Link className="button button--ghost" to={`/studies/${row.id}`}>Заключение</Link><button className="button button--ghost" onClick={() => remove(row)}>Удалить</button></div>
    </article>)}</section>}
  </>;
}
