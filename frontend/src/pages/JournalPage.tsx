import { FormEvent, useCallback, useMemo, useState } from "react";
import { api } from "../api/client";
import type { DayNote, NoteCategory } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { CheckinCard } from "../components/CheckinCard";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";
import { formatDate, moscowToday } from "../lib/format";
import { checkinFields, checkinMetricLabels, noteCategoryLabels } from "../lib/journal";
import { describeCorrelation } from "../components/CorrelationPanel";

const categories = Object.keys(noteCategoryLabels) as NoteCategory[];
const coefficientFormatter = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

export function JournalPage() {
  const today = moscowToday();
  const loadNotes = useCallback((signal: AbortSignal) => api.notes("all", signal), []);
  const notes = useApi(loadNotes);
  const loadCheckins = useCallback((signal: AbortSignal) => api.checkins("90d", signal), []);
  const checkins = useApi(loadCheckins);
  const [editing, setEditing] = useState<DayNote | null>(null);
  const [day, setDay] = useState(today);
  const [category, setCategory] = useState<NoteCategory>("other");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const todayCheckin = useMemo(
    () => checkins.data?.items.find((item) => item.day === today) ?? null,
    [checkins.data, today],
  );

  function reset() {
    setEditing(null);
    setDay(today);
    setCategory("other");
    setText("");
  }

  function startEdit(note: DayNote) {
    setEditing(note);
    setDay(note.day);
    setCategory(note.category);
    setText(note.text);
    setMessage(null);
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) { setError("Напишите короткий текст заметки."); return; }
    setBusy(true); setError(null); setMessage(null);
    try {
      if (editing) await api.updateNote(editing.id, { day, category, text: text.trim() });
      else await api.createNote({ day, category, text: text.trim() });
      setMessage(editing ? "Заметка обновлена." : "Заметка добавлена.");
      reset();
      notes.reload();
    } catch {
      setError("Не удалось сохранить заметку.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(note: DayNote) {
    if (!window.confirm(`Удалить заметку за ${formatDate(note.day)}?`)) return;
    setBusy(true); setError(null);
    try {
      await api.deleteNote(note.id);
      if (editing?.id === note.id) reset();
      setMessage("Заметка удалена.");
      notes.reload();
    } catch {
      setError("Не удалось удалить заметку.");
    } finally {
      setBusy(false);
    }
  }

  const recentCheckins = [...(checkins.data?.items ?? [])].reverse().slice(0, 30);
  const correlations = checkins.data?.correlations ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Личное"
        title="Дневник"
        description="Короткие заметки объясняют скачки на графиках: болезнь, праздник, поездка или новый препарат. Самооценка помогает увидеть, как самочувствие меняется вместе со сном и шагами. Записи видны только вам и не передаются ИИ, в Telegram или отчёт для врача."
      />

      {checkins.loading && !checkins.data ? <LoadingState compact /> : (
        <CheckinCard day={today} initial={todayCheckin} onSaved={checkins.reload} />
      )}

      <section className="journal-layout">
        <form className="panel journal-form" onSubmit={submit} aria-busy={busy}>
          <div className="panel__head"><div><span className="eyebrow">{editing ? "Редактирование" : "Новая запись"}</span><h2>{editing ? "Изменить заметку" : "Заметка к дню"}</h2></div></div>
          <label>Дата<input type="date" value={day} max={today} onChange={(event) => setDay(event.target.value)} required /></label>
          <fieldset className="journal-categories">
            <legend>Категория</legend>
            {categories.map((value) => (
              <label key={value} className={`journal-chip journal-chip--${value}${category === value ? " is-active" : ""}`}>
                <input type="radio" name="note-category" value={value} checked={category === value} onChange={() => setCategory(value)} />
                {noteCategoryLabels[value]}
              </label>
            ))}
          </fieldset>
          <label>Текст<textarea value={text} maxLength={280} rows={3} onChange={(event) => setText(event.target.value)} placeholder="Например: простуда, мало спал, начал новый препарат" required /><small className="label-hint">{text.length}/280</small></label>
          <div className="journal-form__actions">
            <button className="button button--primary" disabled={busy}><Icon name={editing ? "journal" : "plus"} />{busy ? "Сохраняем…" : editing ? "Сохранить" : "Добавить"}</button>
            {editing && <button type="button" className="button button--ghost" onClick={reset} disabled={busy}>Отмена</button>}
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          {message && <p className="form-success" role="status">{message}</p>}
        </form>

        <section className="panel journal-notes" aria-labelledby="journal-notes-title">
          <div className="panel__head"><div><span className="eyebrow">Отмечаются на графиках веса и давления</span><h2 id="journal-notes-title">Заметки</h2></div></div>
          {notes.loading && !notes.data ? <LoadingState compact /> : notes.error && !notes.data ? <ErrorState message={notes.error.message} onRetry={notes.reload} /> : notes.data?.length ? (
            <ol className="note-list">
              {notes.data.map((note) => (
                <li key={note.id} className={editing?.id === note.id ? "is-editing" : undefined}>
                  <span className={`journal-chip journal-chip--${note.category} is-active`}>{noteCategoryLabels[note.category]}</span>
                  <div className="note-list__body"><time dateTime={note.day}>{formatDate(note.day)}</time><p>{note.text}</p></div>
                  <div className="note-list__actions">
                    <button type="button" className="button button--ghost" onClick={() => startEdit(note)} disabled={busy}>Изменить</button>
                    <button type="button" className="icon-button" aria-label={`Удалить заметку за ${formatDate(note.day)}`} onClick={() => remove(note)} disabled={busy}><Icon name="trash" /></button>
                  </div>
                </li>
              ))}
            </ol>
          ) : <EmptyState title="Заметок пока нет" text="Добавьте первую — она появится отметкой на графиках." />}
        </section>
      </section>

      {correlations.length > 0 && (
        <section className="panel correlation-panel" aria-labelledby="checkin-correlations">
          <div className="panel__head correlation-panel__head">
            <div>
              <span className="eyebrow">От 14 совпадающих дней</span>
              <h2 id="checkin-correlations">Самочувствие и данные</h2>
              <p className="correlation-explanation"><strong>r — коэффициент линейной корреляции Пирсона от −1 до 1.</strong> Сравниваются самооценка и показатель за тот же день. Знак показывает направление, |r| — выраженность связи.</p>
            </div>
          </div>
          <div className="correlation-grid">
            {correlations.map((item) => (
              <article className="correlation-card" key={`${item.metric}-${item.target}`}>
                <h3>{checkinMetricLabels[item.metric] ?? item.metric} ↔ {checkinMetricLabels[item.target] ?? item.target}</h3>
                <span className="correlation-card__coefficient">r = {coefficientFormatter.format(item.coefficient)}</span>
                <p>{describeCorrelation(item.coefficient)}</p>
                <small>{item.pairedDays} дней с обоими значениями</small>
              </article>
            ))}
          </div>
          <p className="correlation-note">{correlations[0].disclaimer} Словесная оценка условна и описывает только линейную связь.</p>
        </section>
      )}

      {recentCheckins.length > 0 && (
        <details className="data-table-wrap panel journal-history">
          <summary>История самооценки ({recentCheckins.length})</summary>
          <div className="data-table-scroll">
            <table className="data-table">
              <thead><tr><th>Дата</th>{checkinFields.map((field) => <th key={field.key}>{field.label}</th>)}</tr></thead>
              <tbody>{recentCheckins.map((item) => <tr key={item.day}><td>{formatDate(item.day)}</td>{checkinFields.map((field) => <td key={field.key}>{item[field.key] ?? "—"}</td>)}</tr>)}</tbody>
            </table>
          </div>
        </details>
      )}
    </>
  );
}
