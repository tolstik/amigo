import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { LabDocument } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDateTime } from "../lib/format";

export function LabsUploadPage() {
  const loader = useCallback((signal: AbortSignal) => api.labDocuments(signal), []);
  const documents = useApi(loader);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setInterval(documents.reload, 4_000);
    return () => window.clearInterval(timer);
  }, [documents.reload]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setMessage(null);
    try {
      await api.uploadLab(file);
      setMessage("Файл принят и поставлен в очередь.");
      documents.reload();
    } catch (reason) {
      setMessage(reason instanceof ApiError && reason.status === 409
        ? "Сначала подтвердите обработку данных в профиле."
        : `Не удалось загрузить файл${reason instanceof Error ? `: ${reason.message}` : "."}`);
    } finally {
      setUploading(false);
    }
  }

  async function remove(document: LabDocument) {
    if (!window.confirm(`Удалить «${document.filename}» и все распознанные результаты?`)) return;
    await api.deleteLab(document.id);
    documents.reload();
  }

  return (
    <>
      <PageHeader eyebrow="Архив анализов" title="Загрузка и обработка" description="Поддерживаются текстовые и сканированные PDF до 50 страниц, JPG, PNG и HEIC. Размер файла — до 20 МиБ." actions={<label className={`button button--primary${uploading ? " is-disabled" : ""}`}>{uploading ? "Загружаем…" : "Выбрать файл"}<input className="visually-hidden" type="file" accept=".pdf,.jpg,.jpeg,.png,.heic,.heif,application/pdf,image/jpeg,image/png,image/heic" onChange={upload} disabled={uploading} /></label>} />
      <div className="info-note"><p>Оригиналы хранятся на вашем сервере. OCR выполняет изолированный parser-сервис; в Codex передаётся только извлечённый текст. Результаты появляются с отметкой «не проверено» и требуют вашей проверки.</p></div>
      {message && <p className="form-status" role="status">{message} {message.includes("профиле") && <Link to="/profile">Открыть профиль</Link>}</p>}
      {documents.loading && <LoadingState />}
      {documents.error && <ErrorState onRetry={documents.reload} />}
      {!!documents.data?.length && <section className="document-list">{documents.data.map((document) => <article className="panel document-row" key={document.id}><div><Link to={`/labs/documents/${document.id}`}><strong>{document.filename}</strong></Link><small>{formatDateTime(document.created_at)} · {(document.size_bytes / 1024 / 1024).toFixed(1)} МиБ · {document.result_count} результатов</small></div><span className={`job-status job-status--${document.status}`}>{document.status === "queued" ? "В очереди" : document.status === "processing" ? "Обрабатывается" : document.status === "complete" ? document.verified ? "Проверено" : "Нужно проверить" : "Ошибка"}</span><button className="button button--ghost" onClick={() => remove(document)}>Удалить</button></article>)}</section>}
    </>
  );
}
