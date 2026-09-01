import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Medication, UserProfile } from "../api/types";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { useApi } from "../hooks/useApi";

export function ProfilePage({ onLogout }: { onLogout: () => void }) {
  const loader = useCallback((signal: AbortSignal) => api.profile(signal), []);
  const profile = useApi(loader);
  const medications = useApi(useCallback((signal: AbortSignal) => api.medications(signal), []));
  const [form, setForm] = useState<UserProfile | null>(null);
  const [medicationDrafts, setMedicationDrafts] = useState<Record<string, Medication>>({});
  const [newMedication, setNewMedication] = useState({ name: "", dosage: "", schedule: "" });
  const [medicationBusy, setMedicationBusy] = useState<string | null>(null);
  const [medicationError, setMedicationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => { if (profile.data) setForm(profile.data); }, [profile.data]);
  useEffect(() => {
    if (medications.data) {
      setMedicationDrafts(Object.fromEntries(medications.data.map((item) => [item.id, item])));
    }
  }, [medications.data]);

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

  async function addMedication(event: FormEvent) {
    event.preventDefault();
    if (!newMedication.name.trim() || !newMedication.dosage.trim()) {
      setMedicationError("Укажите название и дозировку.");
      return;
    }
    setMedicationBusy("new");
    setMedicationError(null);
    try {
      await api.createMedication({ ...newMedication, schedule: newMedication.schedule || null });
      setNewMedication({ name: "", dosage: "", schedule: "" });
      medications.reload();
    } catch {
      setMedicationError("Не удалось добавить препарат.");
    } finally {
      setMedicationBusy(null);
    }
  }

  async function saveMedication(item: Medication) {
    if (!item.name.trim() || !item.dosage.trim()) {
      setMedicationError("Название и дозировка не могут быть пустыми.");
      return;
    }
    setMedicationBusy(item.id);
    setMedicationError(null);
    try {
      const updated = await api.updateMedication(item.id, {
        name: item.name,
        dosage: item.dosage,
        schedule: item.schedule || null,
      });
      if (updated) setMedicationDrafts((current) => ({ ...current, [item.id]: updated }));
    } catch {
      setMedicationError("Не удалось сохранить препарат.");
    } finally {
      setMedicationBusy(null);
    }
  }

  async function removeMedication(id: string) {
    if (!window.confirm("Удалить этот препарат из постоянного списка?")) return;
    setMedicationBusy(id);
    setMedicationError(null);
    try {
      await api.deleteMedication(id);
      setMedicationDrafts((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
    } catch {
      setMedicationError("Не удалось удалить препарат.");
    } finally {
      setMedicationBusy(null);
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
      <section className="panel medication-panel" aria-labelledby="medications-title">
        <div className="panel__head">
          <div><span className="eyebrow">Постоянная терапия</span><h2 id="medications-title">Препараты и дозировки</h2><p>Эти записи попадут в пакет для врача и будут доступны приватной AI-аналитике как контекст. Они не используются для автоматических назначений.</p></div>
        </div>
        {medications.loading && !medications.data && <LoadingState compact />}
        {medications.error && !medications.data && <ErrorState message={medications.error.message} onRetry={medications.reload} />}
        {medications.data && <div className="medication-list">
          {Object.values(medicationDrafts).map((item) => <div className="medication-row" key={item.id}>
            <label>Препарат<input value={item.name} onChange={(event) => setMedicationDrafts((current) => ({ ...current, [item.id]: { ...item, name: event.target.value } }))} maxLength={120} /></label>
            <label>Дозировка<input value={item.dosage} onChange={(event) => setMedicationDrafts((current) => ({ ...current, [item.id]: { ...item, dosage: event.target.value } }))} maxLength={80} /></label>
            <label>Режим приёма <span className="label-hint">необязательно</span><input value={item.schedule ?? ""} onChange={(event) => setMedicationDrafts((current) => ({ ...current, [item.id]: { ...item, schedule: event.target.value || null } }))} maxLength={120} placeholder="например, утром" /></label>
            <div className="medication-row__actions"><button className="button button--secondary" type="button" onClick={() => saveMedication(item)} disabled={medicationBusy === item.id}>{medicationBusy === item.id ? "Сохраняем…" : "Сохранить"}</button><button className="button button--ghost" type="button" onClick={() => removeMedication(item.id)} disabled={medicationBusy === item.id}>Удалить</button></div>
          </div>)}
          {!Object.keys(medicationDrafts).length && <p className="panel-note">Постоянные препараты пока не добавлены.</p>}
        </div>}
        <form className="medication-add" onSubmit={addMedication}>
          <div className="medication-add__title"><strong>Добавить препарат</strong><small>Укажите так, как хотите видеть в отчёте.</small></div>
          <label>Препарат<input value={newMedication.name} onChange={(event) => setNewMedication({ ...newMedication, name: event.target.value })} maxLength={120} placeholder="Название" /></label>
          <label>Дозировка<input value={newMedication.dosage} onChange={(event) => setNewMedication({ ...newMedication, dosage: event.target.value })} maxLength={80} placeholder="например, 5 мг" /></label>
          <label>Режим приёма <span className="label-hint">необязательно</span><input value={newMedication.schedule} onChange={(event) => setNewMedication({ ...newMedication, schedule: event.target.value })} maxLength={120} placeholder="например, 1 раз в день" /></label>
          <button className="button button--primary" disabled={medicationBusy === "new"}>{medicationBusy === "new" ? "Добавляем…" : "Добавить"}</button>
        </form>
        {medicationError && <p className="form-error" role="alert">{medicationError}</p>}
      </section>
    </>
  );
}
