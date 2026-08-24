import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { HealthTask, HealthTaskInput, TaskStateFilter } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { TaskDialog } from "../components/TaskDialog";
import { useApi } from "../hooks/useApi";
import { formatDateTime } from "../lib/format";

const recurrenceLabels = { once: "Один раз", daily: "Каждый день", weekly: "Каждую неделю", monthly: "Каждый месяц" } as const;
const statusLabels = { active: "Открыта", completed: "Выполнена", cancelled: "Отменена" } as const;

export function TasksPage() {
  const [state, setState] = useState<TaskStateFilter>("open");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<HealthTask | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const load = useCallback((signal: AbortSignal) => api.tasks(state, signal), [state]);
  const tasks = useApi(load);

  async function create(input: HealthTaskInput) {
    await api.createTask(input);
    setStatus("Задача создана.");
    if (state !== "open") setState("open"); else tasks.reload();
  }

  async function update(input: HealthTaskInput) {
    if (!editing) return;
    await api.updateTask(editing.id, {
      title: input.title,
      note: input.note,
      next_due_at: input.next_due_at,
      recurrence: input.recurrence,
      telegram_enabled: input.telegram_enabled,
    });
    setStatus("Изменения сохранены.");
    tasks.reload();
  }

  async function mutate(task: HealthTask, action: "complete" | "cancel") {
    if (action === "cancel" && !window.confirm(`Отменить задачу «${task.title}»?`)) return;
    setBusy(task.id);
    setActionError(null);
    try {
      await (action === "complete" ? api.completeTask(task.id) : api.cancelTask(task.id));
      setStatus(action === "complete" ? "Выполнение отмечено." : "Задача отменена.");
      tasks.reload();
    } catch {
      setActionError("Не удалось изменить задачу. Обновите список и попробуйте ещё раз.");
    } finally {
      setBusy(null);
    }
  }

  const rows = tasks.data?.items ?? [];
  return <>
    <PageHeader eyebrow="Планы и напоминания" title="Задачи" description="Вы сами решаете, какие действия сохранить. Медицинские задачи не создаются автоматически; Telegram-напоминание можно отключить." actions={<button className="button button--primary" type="button" onClick={() => setCreating(true)}>Создать задачу</button>} />
    <div className="tasks-toolbar panel">
      <div className="segmented" role="group" aria-label="Состояние задач">
        {(["open", "completed", "all"] as const).map((value) => <button key={value} type="button" className={state === value ? "is-active" : ""} aria-pressed={state === value} onClick={() => setState(value)}>{value === "open" ? `Открытые${tasks.data ? ` · ${tasks.data.openCount}` : ""}` : value === "completed" ? "Выполненные" : "Все"}</button>)}
      </div>
    </div>
    <div className="sr-status" aria-live="polite">{status}</div>
    {actionError && <p className="form-error" role="alert">{actionError}</p>}
    {tasks.loading && !tasks.data ? <LoadingState /> : tasks.error && !tasks.data ? <ErrorState message={tasks.error.message} onRetry={tasks.reload} /> : rows.length ? <section className="task-list" aria-label="Список задач">
      {rows.map((task) => <article className={`task-card panel task-card--${task.status}${task.overdue ? " task-card--overdue" : ""}`} key={task.id}>
        <div className="task-card__main">
          <div className="task-card__badges"><span>{statusLabels[task.status]}</span>{task.overdue && <strong>Просрочена</strong>}{task.telegramEnabled && <span>Telegram</span>}</div>
          <h2>{task.title}</h2>
          {task.note && <p>{task.note}</p>}
          <dl><div><dt>Срок</dt><dd>{task.nextDueAt ? formatDateTime(task.nextDueAt) : "—"}</dd></div><div><dt>Повтор</dt><dd>{recurrenceLabels[task.recurrence]}</dd></div></dl>
          {task.source && <details className="task-source"><summary>Исходная AI-рекомендация</summary><strong>{task.source.title}</strong><p>{task.source.text}</p><small>Зафиксирована {task.source.generatedAt ? formatDateTime(task.source.generatedAt) : "при создании задачи"}</small></details>}
        </div>
        {task.status === "active" && <div className="task-card__actions">
          <button className="button button--primary" type="button" disabled={busy === task.id} onClick={() => mutate(task, "complete")}>Выполнено</button>
          <button className="button button--secondary" type="button" disabled={busy === task.id} onClick={() => setEditing(task)}>Изменить</button>
          <button className="button button--ghost" type="button" disabled={busy === task.id} onClick={() => mutate(task, "cancel")}>Отменить</button>
        </div>}
      </article>)}
    </section> : <EmptyState title={state === "open" ? "Открытых задач нет" : "Задач в этом разделе нет"} text="Создайте задачу вручную или сохраните валидированную рекомендацию из обзора." />}
    {creating && <TaskDialog initial={{ title: "", note: null }} onSubmit={create} onClose={() => setCreating(false)} />}
    {editing && <TaskDialog heading="Изменить задачу" submitLabel="Сохранить" initial={{ title: editing.title, note: editing.note, nextDueAt: editing.nextDueAt, recurrence: editing.recurrence, telegramEnabled: editing.telegramEnabled }} onSubmit={update} onClose={() => setEditing(null)} />}
  </>;
}
