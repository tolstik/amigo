import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, labEventsUrl } from "../api/client";
import type { LabDocument } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDateTime } from "../lib/format";

type UploadState = { name: string; status: "waiting" | "uploading" | "accepted" | "failed"; error?: string };
const MAX_FILES = 25;
const MAX_FILE_BYTES = 20 * 1024 * 1024;

const stageLabel: Record<string, string> = {
  queued: "Ожидает обработки",
  reading: "Распознаём документ",
  extracting: "Извлекаем показатели",
  complete: "Обработка завершена",
  failed: "Ошибка обработки",
};

export function LabsUploadPage() {
  const loader = useCallback((signal: AbortSignal) => api.labDocuments(signal), []);
  const documents = useApi(loader);
  const [uploading, setUploading] = useState(false);
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const events = new EventSource(labEventsUrl(), { withCredentials: true });
    events.addEventListener("queue", documents.reload);
    return () => events.close();
  }, [documents.reload]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []).slice(0, MAX_FILES);
    event.target.value = "";
    if (!selected.length) return;
    if (selected.some((file) => file.size > MAX_FILE_BYTES)) {
      setMessage("Каждый файл должен быть не больше 20 МиБ.");
      return;
    }
    setUploading(true);
    setMessage(null);
    setUploads(selected.map((file) => ({ name: file.name, status: "waiting" })));
    let accepted = 0;
    for (let index = 0; index < selected.length; index += 1) {
      setUploads((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status: "uploading" } : item));
      try {
        await api.uploadLab(selected[index]);
        accepted += 1;
        setUploads((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status: "accepted" } : item));
      } catch (reason) {
        const error = reason instanceof ApiError && reason.status === 409
          ? "Нужно подтвердить обработку данных в профиле"
          : reason instanceof Error ? reason.message : "Ошибка загрузки";
        setUploads((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status: "failed", error } : item));
      }
    }
    setMessage(`${accepted} из ${selected.length} файлов приняты и поставлены в очередь.`);
    setUploading(false);
    documents.reload();
  }

  async function remove(document: LabDocument) {
    if (!window.confirm(`Удалить «${document.filename}» и все распознанные результаты?`)) return;
    await api.deleteLab(document.id);
    documents.reload();
  }

  return <>
    <PageHeader
      eyebrow="Архив анализов"
      title="Загрузка и обработка"
      description="Выберите до 25 PDF, JPG, PNG или HEIC одновременно. Каждый файл — до 20 МиБ; PDF — до 50 страниц."
      actions={<label className={`button button--primary${uploading ? " is-disabled" : ""}`}>{uploading ? "Загружаем очередь…" : "Выбрать файлы"}<input className="visually-hidden" type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.heic,.heif,application/pdf,image/jpeg,image/png,image/heic" onChange={upload} disabled={uploading} /></label>}
    />
    <div className="info-note"><p>Оригинал сразу сохраняется в PostgreSQL и временно дублируется в защищённом каталоге для совместимости с предыдущим релизом. Обработка идёт последовательно и не мешает добавлять новые файлы.</p></div>
    {message && <p className="form-status" role="status">{message} {message.includes("профиле") && <Link to="/profile">Открыть профиль</Link>}</p>}
    {!!uploads.length && <section className="panel upload-batch"><h2>Загрузка файлов</h2>{uploads.map((item, index) => <div className="upload-batch__row" key={`${item.name}-${index}`}><span>{item.name}</span><strong>{item.status === "waiting" ? "Ожидает" : item.status === "uploading" ? "Загружается" : item.status === "accepted" ? "В очереди" : item.error ?? "Ошибка"}</strong></div>)}</section>}
    {documents.loading && !documents.data && <LoadingState />}
    {documents.error && <ErrorState onRetry={documents.reload} />}
    {!!documents.data?.length && <section className="document-list" aria-label="Очередь обработки">{documents.data.map((document) => <article className="panel document-row" key={document.id}>
      <div><strong>{document.filename}</strong><small>{formatDateTime(document.created_at)} · {(document.size_bytes / 1024 / 1024).toFixed(1)} МиБ · {document.result_count} результатов</small>{document.queue_position && document.status === "queued" ? <small>Позиция в очереди: {document.queue_position}</small> : null}<div className="queue-progress" aria-label={`${document.progress_percent}%`}><span style={{ width: `${document.progress_percent}%` }} /></div></div>
      <span className={`job-status job-status--${document.status}`}>{document.status === "complete" ? document.verified ? "Проверено" : "Нужно проверить" : stageLabel[document.processing_stage] ?? document.status}</span>
      <div className="document-row__actions"><Link className="button button--secondary" to={`/labs/documents/${document.id}/view`}>Посмотреть</Link><Link className="button button--ghost" to={`/labs/documents/${document.id}`}>Результаты</Link><button className="button button--ghost" onClick={() => remove(document)}>Удалить</button></div>
    </article>)}</section>}
  </>;
}
