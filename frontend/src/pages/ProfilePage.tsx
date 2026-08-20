import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { UserProfile } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";

export function ProfilePage({ onLogout }: { onLogout: () => void }) {
  const loader = useCallback((signal: AbortSignal) => api.profile(signal), []);
  const profile = useApi(loader);
  const [form, setForm] = useState<UserProfile | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => { if (profile.data) setForm(profile.data); }, [profile.data]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setSaved(false);
    setError(null);
    try {
      const updated = await api.updateProfile({
        birth_date: form.birth_date,
        reference_sex: form.reference_sex,
        accept_ai_data_processing: Boolean(form.ai_data_consent_version),
      });
      setForm(updated);
      setSaved(true);
    } catch {
      setError("Не удалось сохранить профиль.");
    }
  }

  async function logout() {
    setLoggingOut(true);
    setError(null);
    try {
      await api.logout();
      onLogout();
    } catch {
      setError("Не удалось завершить сессию.");
      setLoggingOut(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Настройки" title="Профиль и приватность" description="Дата рождения и биологический пол используются только для точного выбора лабораторных референсов. Рост приходит из серверного профиля." />
      {profile.loading && <LoadingState />}
      {profile.error && <ErrorState onRetry={profile.reload} />}
      {form && !profile.loading && !profile.error && <form className="panel settings-form" onSubmit={submit}>
          <label>Дата рождения<input type="date" value={form.birth_date ?? ""} onChange={(event) => setForm({ ...form, birth_date: event.target.value || null })} required /></label>
          <label>Биологический пол для референсов<select value={form.reference_sex ?? "unspecified"} onChange={(event) => setForm({ ...form, reference_sex: event.target.value as UserProfile["reference_sex"] })}><option value="unspecified">Не указан</option><option value="male">Мужской</option><option value="female">Женский</option></select></label>
          <label>Рост<input value={`${form.height_cm} см`} disabled /></label>
          <label className="consent-check"><input type="checkbox" checked={Boolean(form.ai_data_consent_version)} onChange={(event) => setForm({ ...form, ai_data_consent_version: event.target.checked ? "pending" : null })} /><span><strong>Разрешаю обработку данных локальным Codex-контуром</strong><small>Codex CLI запускается на вашем сервере, однако извлечённый текст анализов и вопросы передаются в OpenAI inference. Текст может содержать персональные и медицинские данные.</small></span></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          {saved && <p className="form-success" role="status">Профиль сохранён.</p>}
          <button className="button button--primary">Сохранить</button>
          <button className="button button--ghost profile-logout" type="button" onClick={logout} disabled={loggingOut}>{loggingOut ? "Выходим…" : "Выйти из Amigo"}</button>
        </form>}
    </>
  );
}
