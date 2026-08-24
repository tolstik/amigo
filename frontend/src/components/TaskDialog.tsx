import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { HealthTaskInput, TaskRecurrence } from "../api/types";

export interface TaskDialogInitial {
  title: string;
  note: string | null;
  nextDueAt?: string | null;
  recurrence?: TaskRecurrence;
  telegramEnabled?: boolean;
}

export interface TaskDialogSource {
  analysisId: number;
  itemId: string;
  title: string;
  text: string;
}

function moscowInputValue(value?: string | null): string {
  const date = value ? new Date(value) : new Date(Date.now() + 24 * 60 * 60 * 1000);
  if (!value) date.setMinutes(0, 0, 0);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date).reduce<Record<string, string>>((result, part) => {
    result[part.type] = part.value;
    return result;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function errorMessage(reason: unknown): string {
  if (reason instanceof ApiError && reason.message === "next_due_at_must_be_future") return "Выберите будущие дату и время.";
  return "Не удалось сохранить задачу. Проверьте поля и попробуйте ещё раз.";
}

export function TaskDialog({
  initial,
  source,
  heading = "Создать задачу",
  submitLabel = "Создать задачу",
  onSubmit,
  onClose,
}: {
  initial: TaskDialogInitial;
  source?: TaskDialogSource;
  heading?: string;
  submitLabel?: string;
  onSubmit: (input: HealthTaskInput) => Promise<void>;
  onClose: () => void;
}) {
  const titleId = useId();
  const titleInput = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState(initial.title);
  const [note, setNote] = useState(initial.note ?? "");
  const [due, setDue] = useState(moscowInputValue(initial.nextDueAt));
  const [recurrence, setRecurrence] = useState<TaskRecurrence>(initial.recurrence ?? "once");
  const [telegram, setTelegram] = useState(initial.telegramEnabled ?? true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    titleInput.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !submitting) onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, submitting]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const dueDate = new Date(`${due}+03:00`);
    if (!title.trim()) return setError("Введите название задачи.");
    if (!Number.isFinite(dueDate.getTime()) || dueDate.getTime() <= Date.now()) return setError("Выберите будущие дату и время.");
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        title: title.trim(),
        note: note.trim() || null,
        next_due_at: dueDate.toISOString(),
        recurrence,
        telegram_enabled: telegram,
        ...(source ? { source_analysis_id: source.analysisId, source_item_id: source.itemId } : {}),
      });
      onClose();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return <div className="drawer-backdrop task-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose(); }}>
    <section className="task-dialog panel" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={submitting}>
      <div className="task-dialog__head"><div><span className="eyebrow">Личное действие</span><h2 id={titleId}>{heading}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть" disabled={submitting}>×</button></div>
      {source && <div className="task-dialog__source"><strong>{source.title}</strong><p>{source.text}</p><small>Задача будет связана с валидированной рекомендацией после вашего подтверждения.</small></div>}
      <form className="task-form" onSubmit={submit}>
        <label>Название<input ref={titleInput} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required /></label>
        <label>Заметка<textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} rows={3} /></label>
        <div className="task-form__row">
          <label>Дата и время (МСК)<input type="datetime-local" value={due} onChange={(event) => setDue(event.target.value)} required /></label>
          <label>Повтор<select value={recurrence} onChange={(event) => setRecurrence(event.target.value as TaskRecurrence)}><option value="once">Один раз</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label>
        </div>
        <label className="check-row"><input type="checkbox" checked={telegram} onChange={(event) => setTelegram(event.target.checked)} /><span><strong>Напомнить в Telegram</strong><small>Уведомление придёт к выбранному времени.</small></span></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="task-form__actions"><button className="button button--ghost" type="button" onClick={onClose} disabled={submitting}>Отмена</button><button className="button button--primary" disabled={submitting}>{submitting ? "Сохраняем…" : submitLabel}</button></div>
      </form>
    </section>
  </div>;
}
