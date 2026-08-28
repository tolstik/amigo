import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, csvUrl } from "../api/client";
import { circumferenceChartOption } from "../charts/options";
import { ChartCard } from "../components/ChartCard";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { PeriodSwitcher } from "../components/PeriodSwitcher";
import { useApi } from "../hooks/useApi";
import { useChartPeriod } from "../hooks/useChartPeriod";
import type { CircumferencePoint } from "../api/types";
import { formatDate, formatNumber } from "../lib/format";

function todayLocal(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function CircumferencePage() {
  const [period, setPeriod] = useChartPeriod("1y");
  const load = useCallback((signal: AbortSignal) => api.circumference(period, signal), [period]);
  const series = useApi(load);
  const [date, setDate] = useState(todayLocal);
  const [waist, setWaist] = useState("");
  const [hip, setHip] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const points = series.data?.points ?? [];
  const selected = useMemo(() => points.find((point) => point.measuredOn === date), [date, points]);

  useEffect(() => {
    if (!series.data) return;
    const point = series.data.points.find((item) => item.measuredOn === date);
    setWaist(point?.waistCm == null ? "" : String(point.waistCm));
    setHip(point?.hipCm == null ? "" : String(point.hipCm));
  }, [date, series.data]);

  function chooseDate(value: string) {
    setDate(value);
    const point = points.find((item) => item.measuredOn === value);
    setWaist(point?.waistCm == null ? "" : String(point.waistCm));
    setHip(point?.hipCm == null ? "" : String(point.hipCm));
    setMessage(null);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const waistValue = waist.trim() ? Number(waist.replace(",", ".")) : null;
    const hipValue = hip.trim() ? Number(hip.replace(",", ".")) : null;
    if (waistValue === null && hipValue === null) return setError("Укажите талию или бёдра.");
    if ([waistValue, hipValue].some((value) => value !== null && (!Number.isFinite(value) || value < 20 || value > 300))) return setError("Введите значение от 20 до 300 см.");
    setBusy(true); setError(null); setMessage(null);
    try {
      await api.saveCircumference(date, { waist_cm: waistValue, hip_cm: hipValue });
      setMessage("Измерение сохранено.");
      series.reload();
    } catch { setError("Не удалось сохранить измерение."); } finally { setBusy(false); }
  }

  async function remove() {
    if (!selected || !window.confirm(`Удалить измерение за ${formatDate(date)}?`)) return;
    setBusy(true); setError(null);
    try { await api.deleteCircumference(date); setWaist(""); setHip(""); setMessage("Измерение удалено."); series.reload(); }
    catch { setError("Не удалось удалить измерение."); } finally { setBusy(false); }
  }

  return <>
    <PageHeader eyebrow="Ручные измерения" title="Обхваты тела" description="Измеряйте талию и бёдра в одинаковых условиях, чтобы видеть спокойный долгосрочный тренд." actions={<a className="button button--secondary" href={csvUrl("circumference", period)} download><Icon name="download" /> Скачать CSV</a>} />
    <section className="circumference-layout">
      <form className="panel circumference-form" onSubmit={save} aria-busy={busy}>
        <div className="panel__head"><div><span className="eyebrow">Ежедневная запись</span><h2>Добавить измерение</h2><p>Можно заполнить только одно поле.</p></div></div>
        <label>Дата<input type="date" value={date} max={todayLocal()} onChange={(event) => chooseDate(event.target.value)} required /></label>
        <label>Талия, см<input type="number" min="20" max="300" step="0.1" inputMode="decimal" value={waist} onChange={(event) => setWaist(event.target.value)} placeholder="например, 96.5" /></label>
        <label>Бёдра, см<input type="number" min="20" max="300" step="0.1" inputMode="decimal" value={hip} onChange={(event) => setHip(event.target.value)} placeholder="например, 108.0" /></label>
        <div className="circumference-form__actions"><button className="button button--primary" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить"}</button>{selected && <button type="button" className="button button--ghost" disabled={busy} onClick={remove}>Удалить дату</button>}</div>
        {error && <p className="form-error" role="alert">{error}</p>}{message && <p className="form-success" role="status">{message}</p>}
      </form>
      <aside className="panel circumference-note"><span className="circumference-note__mark"><Icon name="activity" /></span><div><h2>Как измерять</h2><p>Лента параллельно полу, без втягивания живота и поверх тонкой одежды. Записывайте результат после спокойного выдоха.</p></div></aside>
    </section>
    <div className="toolbar"><PeriodSwitcher value={period} onChange={setPeriod} /></div>
    {series.loading && !series.data ? <LoadingState /> : series.error && !series.data ? <ErrorState onRetry={series.reload} /> : points.length ? <>
      <section className="kpi-grid kpi-grid--three" aria-label="Последние обхваты">
        <article className="kpi-card kpi-card--coral"><span className="kpi-card__label">Последняя талия</span><strong className="kpi-card__value">{points.at(-1)?.waistCm == null ? "—" : `${formatNumber(points.at(-1)!.waistCm)} см`}</strong><small className="kpi-card__hint">{points.at(-1)?.waistCm == null ? "Нет данных" : formatDate(points.at(-1)!.measuredOn)}</small></article>
        <article className="kpi-card kpi-card--violet"><span className="kpi-card__label">Последние бёдра</span><strong className="kpi-card__value">{points.at(-1)?.hipCm == null ? "—" : `${formatNumber(points.at(-1)!.hipCm)} см`}</strong><small className="kpi-card__hint">{points.at(-1)?.hipCm == null ? "Нет данных" : formatDate(points.at(-1)!.measuredOn)}</small></article>
        <article className="kpi-card kpi-card--green"><span className="kpi-card__label">Дней с записями</span><strong className="kpi-card__value">{points.length}</strong><small className="kpi-card__hint">За выбранный период</small></article>
      </section>
      <ChartCard title="История обхватов" subtitle={`${series.data?.meta.count ?? points.length} записей · пропуски не соединяются`} option={circumferenceChartOption(points)} ariaLabel="График талии и бёдер в сантиметрах" height={420} footer={<CircumferenceTable points={points} />} />
    </> : <EmptyState title="Записей обхватов пока нет" text="Добавьте первую талию или обхват бёдер — здесь появится исторический график." />}
  </>;
}

function CircumferenceTable({ points }: { points: CircumferencePoint[] }) {
  return <details className="data-table-wrap"><summary>Показать таблицу измерений ({points.length})</summary><div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Талия</th><th>Бёдра</th></tr></thead><tbody>{[...points].reverse().slice(0, 100).map((point) => <tr key={point.measuredOn}><td>{formatDate(point.measuredOn)}</td><td>{point.waistCm == null ? "—" : `${formatNumber(point.waistCm)} см`}</td><td>{point.hipCm == null ? "—" : `${formatNumber(point.hipCm)} см`}</td></tr>)}</tbody></table></div></details>;
}
