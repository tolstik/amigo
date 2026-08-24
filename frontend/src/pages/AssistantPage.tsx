import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, assistantEventsUrl } from "../api/client";
import type { AssistantMessage, AssistantSegment } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { EvidenceChips } from "../components/EvidenceChips";
import { TaskDialog, type TaskDialogSource } from "../components/TaskDialog";
import { useApi } from "../hooks/useApi";

export function AssistantPage() {
  const loader = useCallback((signal: AbortSignal) => api.assistantMessages(signal), []);
  const messages = useApi(loader);
  const [question, setQuestion] = useState("");
  const [drafts, setDrafts] = useState<Record<string, AssistantSegment[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [taskSource, setTaskSource] = useState<TaskDialogSource | null>(null);
  const [taskNotice, setTaskNotice] = useState<string | null>(null);
  const streams = useRef<Map<string, EventSource>>(new Map());
  const inFlight = messages.data?.items.find((item) => item.role === "assistant" && ["queued", "streaming", "validating"].includes(item.status));

  const watch = useCallback((message: AssistantMessage) => {
    if (streams.current.has(message.id)) return;
    const source = new EventSource(assistantEventsUrl(message.id), { withCredentials: true });
    streams.current.set(message.id, source);
    source.addEventListener("draft_segment", (event) => {
      const segment = JSON.parse((event as MessageEvent).data) as AssistantSegment;
      setDrafts((current) => ({ ...current, [message.id]: [...(current[message.id] ?? []), segment] }));
    });
    source.addEventListener("reset", () => setDrafts((current) => ({ ...current, [message.id]: [] })));
    source.addEventListener("complete", () => { source.close(); streams.current.delete(message.id); messages.reload(); });
    source.addEventListener("error", () => { source.close(); streams.current.delete(message.id); window.setTimeout(messages.reload, 600); });
  }, [messages.reload]);

  useEffect(() => { if (inFlight) watch(inFlight); }, [inFlight?.id, watch]);
  useEffect(() => () => { streams.current.forEach((source) => source.close()); streams.current.clear(); }, []);

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = question.trim();
    if (!content || inFlight) return;
    setError(null);
    try {
      const message = await api.sendAssistantMessage(content, crypto.randomUUID());
      setQuestion("");
      messages.reload();
      watch(message);
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409
        ? reason.message === "ai_data_consent_required" ? "Сначала подтвердите обработку данных в профиле." : "Дождитесь завершения текущего ответа."
        : "Не удалось отправить вопрос.");
    }
  }

  async function clear() {
    if (!window.confirm("Удалить всю переписку с ассистентом?")) return;
    await api.clearAssistantHistory();
    setDrafts({});
    messages.reload();
  }

  const rows = messages.data?.items ?? [];
  return <>
    <PageHeader eyebrow="Codex · персональный контекст" title="Ассистент здоровья" description="Рекомендации учитывают измерения, лабораторную историю и подтверждённый профиль. Ответы не заменяют консультацию специалиста." actions={rows.length ? <button className="button button--ghost" onClick={clear} disabled={Boolean(inFlight)}>Очистить чат</button> : undefined} />
    <div className="emergency-note"><strong>Важно</strong><span>Ассистент не предназначен для экстренной оценки. При острых или быстро усиливающихся симптомах используйте местную службу экстренной помощи.</span></div>
    {messages.loading && <LoadingState />}
    {messages.error && <ErrorState onRetry={messages.reload} />}
    {!!messages.data?.recommendations.length && <section className="assistant-recommendations"><h2>Актуальные рекомендации</h2><div className="insight-grid">{messages.data.recommendations.map((item) => <article className="insight insight--recommendation" key={item.id}><div className="insight__body"><strong>{item.title}</strong><p>{item.text}</p><EvidenceChips evidenceIds={item.evidenceIds} evidence={messages.data?.evidence ?? {}} />{messages.data?.analysisId !== null && messages.data?.analysisId !== undefined && <button className="insight__task" type="button" onClick={() => { setTaskNotice(null); setTaskSource({ analysisId: messages.data!.analysisId!, itemId: item.id, title: item.title, text: item.text }); }}>Создать задачу</button>}</div></article>)}</div></section>}
    {!messages.loading && <section className="panel chat-panel">
      <div className="chat-messages" aria-live="polite">
        {!rows.length && <div className="chat-empty"><strong>Контекст уже собран</strong><p>Можно спросить о динамике показателей, подготовке к визиту или о том, какие значения стоит перепроверить.</p></div>}
        {rows.map((message) => {
          const visibleDrafts = drafts[message.id] ?? message.draft_segments ?? [];
          const content = message.status === "complete" ? message.content : visibleDrafts.map((segment) => segment.text).join("\n\n");
          return <article className={`chat-message chat-message--${message.role}`} key={message.id}><span>{message.role === "user" ? "Вы" : "Amigo"}</span>{content ? <p>{content}</p> : <p className="chat-typing"><i /><i /><i /></p>}{message.role === "assistant" && message.status === "complete" && <EvidenceChips evidenceIds={message.evidence_keys} evidence={message.evidence} />}{message.role === "assistant" && message.status !== "complete" && <small>{message.status === "validating" ? "Проверяем финальный ответ…" : visibleDrafts.length ? "Черновик · ответ формируется" : "Готовим ответ…"}</small>}{message.status === "failed" && <button className="button button--ghost" onClick={async () => { const retried = await api.retryAssistantMessage(message.id); messages.reload(); watch(retried); }}>Повторить</button>}</article>;
        })}
      </div>
      <form className="chat-composer" onSubmit={send}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} maxLength={4000} placeholder="Задайте вопрос по вашим данным…" disabled={Boolean(inFlight)} /><div><small>Структурированные данные и релевантные фрагменты анализов будут добавлены автоматически.</small><button className="button button--primary" disabled={!question.trim() || Boolean(inFlight)}>Отправить</button></div></form>
      {error && <p className="form-error">{error} {error.includes("профиле") && <Link to="/profile">Открыть профиль</Link>}</p>}
    </section>}
    <div className="sr-status" role="status" aria-live="polite">{taskNotice}</div>
    {taskSource && <TaskDialog initial={{ title: taskSource.title, note: taskSource.text }} source={taskSource} onSubmit={async (input) => { await api.createTask(input); setTaskNotice("Задача создана и доступна в разделе «Задачи»."); }} onClose={() => setTaskSource(null)} />}
  </>;
}
