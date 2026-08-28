import { FormEvent, useState } from "react";
import { api } from "../api/client";
import type { DoctorReport, DoctorReportPeriod, DoctorReportSection } from "../api/types";
import { activityDailyChartOption, circumferenceChartOption, pressureChartOption, sleepChartOption, weightActualChartOption, weightChartOption } from "../charts/options";
import { ChartCard } from "../components/ChartCard";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";

const sectionOptions: Array<{ key: DoctorReportSection; label: string; note: string }> = [
  { key: "summary", label: "Краткая сводка", note: "Рост, последний вес, давление и состав тела" },
  { key: "weight", label: "Вес", note: "Динамика и план" },
  { key: "circumference", label: "Обхваты", note: "Талия и бёдра в сантиметрах" },
  { key: "pressure", label: "Давление", note: "Систолическое и диастолическое" },
  { key: "activity", label: "Активность", note: "Шаги только из Xiaomi Cloud" },
  { key: "recovery", label: "Сон и восстановление", note: "Сон в часах в графиках" },
  { key: "labs", label: "Анализы", note: "Только подтверждённые или исправленные" },
  { key: "studies", label: "Исследования", note: "Только подтверждённые" },
  { key: "ai", label: "AI-рекомендации", note: "Необязательно; только готовый валидированный результат" },
];

const defaultSections = sectionOptions.filter((item) => item.key !== "ai").map((item) => item.key);
const modalityLabels = { ultrasound: "УЗИ", mri: "МРТ", ct: "КТ", xray: "Рентген", ecg: "ЭКГ", other: "Исследование" } as const;
const labStatusLabels = { within_reference: "В референсе", below_reference: "Ниже", above_reference: "Выше", outside_reference: "Вне референса", indeterminate: "Не определён" } as const;

function bytes(value: number): string {
  if (value < 1024 * 1024) return `${formatNumber(value / 1024, 0)} КБ`;
  return `${formatNumber(value / 1024 / 1024, 1)} МБ`;
}

function nested(source: Record<string, unknown> | null, key: string): Record<string, unknown> {
  const value = source?.[key];
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function numeric(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function DoctorReportPage() {
  const [period, setPeriod] = useState<DoctorReportPeriod>("90d");
  const [sections, setSections] = useState<DoctorReportSection[]>(defaultSections);
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  function toggle(section: DoctorReportSection) {
    setSections((current) => current.includes(section) ? current.filter((value) => value !== section) : [...current, section]);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!sections.length) return setError("Выберите хотя бы один раздел.");
    setCreating(true);
    setError(null);
    setStatus(null);
    try {
      setReport(await api.createDoctorReport(period, sectionOptions.map((item) => item.key).filter((key) => sections.includes(key))));
      setStatus("Новый пакет сформирован.");
    } catch {
      setError("Не удалось собрать пакет. Уменьшите период или число разделов и попробуйте ещё раз.");
    } finally {
      setCreating(false);
    }
  }

  async function removeReport() {
    if (!report || !window.confirm("Удалить этот пакет раньше срока? Ссылка на HTML перестанет работать.")) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteDoctorReport(report.id);
      setReport(null);
      setStatus("Пакет удалён.");
    } catch {
      setError("Не удалось удалить пакет. Попробуйте ещё раз.");
    } finally {
      setDeleting(false);
    }
  }

  const summary = report?.preview.summary ?? null;
  const summaryWeight = nested(summary, "weight");
  const summaryPressure = nested(summary, "pressure");
  return <>
    <PageHeader eyebrow="Подготовка к визиту" title="Пакет для врача" description="Соберите самостоятельный HTML-дашборд из одного зафиксированного набора данных. Он открывается без интернета и аккуратно печатается на A4. Оригиналы, OCR, имена файлов, чат и идентификаторы источников не включаются." />
    <form className="report-builder panel" onSubmit={create} aria-busy={creating}>
      <fieldset><legend>Период</legend><div className="segmented" role="group" aria-label="Период пакета">{(["30d", "90d", "1y"] as const).map((value) => <button key={value} type="button" aria-pressed={period === value} className={period === value ? "is-active" : ""} onClick={() => setPeriod(value)}>{value === "30d" ? "30 дней" : value === "90d" ? "90 дней" : "1 год"}</button>)}</div></fieldset>
      <fieldset><legend>Разделы</legend><div className="report-sections">{sectionOptions.map((item) => <label key={item.key} className={item.key === "ai" ? "report-section report-section--optional" : "report-section"}><input type="checkbox" checked={sections.includes(item.key)} onChange={() => toggle(item.key)} /><span><strong>{item.label}</strong><small>{item.note}</small></span></label>)}</div></fieldset>
      <div className="report-builder__actions"><p>HTML действует 24 часа и формируется из неизменяемого preview.</p><button className="button button--primary" disabled={creating || !sections.length}>{creating ? "Формируем…" : "Сформировать preview и HTML"}</button></div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
    <div className="sr-status" aria-live="polite">{status}</div>
    {report && <article className="doctor-preview" aria-labelledby="doctor-preview-title">
      <div className="doctor-preview__bar panel" aria-busy={deleting}><div><span className="eyebrow">Готово · HTML · {bytes(report.htmlSizeBytes)}</span><h2 id="doctor-preview-title">Preview пакета</h2><p>{formatDate(report.preview.meta.from)} — {formatDate(report.preview.meta.to)} · доступен до {formatDateTime(report.expiresAt)}</p></div><div><button className="button button--ghost" type="button" onClick={removeReport} disabled={deleting}>{deleting ? "Удаляем…" : "Удалить пакет"}</button><button className="button button--ghost" type="button" onClick={() => window.print()} disabled={deleting}>Печать preview</button><a className={`button button--primary${deleting ? " is-disabled" : ""}`} href={report.htmlDownloadUrl} download aria-disabled={deleting} onClick={(event) => { if (deleting) event.preventDefault(); }}><Icon name="download" /> Скачать HTML</a></div></div>
      {report.preview.meta.unverifiedLabsCount > 0 && <aside className="report-labs-warning panel" role="status"><strong>{report.preview.meta.unverifiedLabsCount} строк отмечены как «Не проверено»</strong><p>Они включены в экспорт с исходным статусом. Сверьте значения с бланком перед визитом к врачу.</p></aside>}
      {summary && <section className="report-summary panel" aria-labelledby="report-summary-title"><div className="panel__head"><div><span className="eyebrow">Зафиксированная сводка</span><h2 id="report-summary-title">Основные показатели</h2></div></div><dl><div><dt>Рост</dt><dd>{numeric(summary, "height_cm") === null ? "—" : `${formatNumber(numeric(summary, "height_cm"), 0)} см`}</dd></div><div><dt>Последний вес</dt><dd>{numeric(summaryWeight, "latest_kg") === null ? "—" : `${formatNumber(numeric(summaryWeight, "latest_kg"))} кг`}</dd></div><div><dt>Последнее давление</dt><dd>{numeric(summaryPressure, "latest_systolic") === null ? "—" : `${formatNumber(numeric(summaryPressure, "latest_systolic"), 0)} / ${formatNumber(numeric(summaryPressure, "latest_diastolic"), 0)}`}</dd></div></dl></section>}
      {report.preview.weight && ((report.preview.weight.raw.length || report.preview.weight.points.length) ? <ChartCard title="Вес" subtitle="Реальные измерения Withings · без усреднения и плана" option={weightActualChartOption(report.preview.weight.raw.length ? report.preview.weight.raw : report.preview.weight.points.map((point) => ({ measuredAt: point.measuredAt, valueKg: point.weightKg })))} ariaLabel="График реальных измерений веса в пакете для врача" height={330} /> : <section className="report-empty panel"><h2>Вес</h2><p>За выбранный период данных нет.</p></section>)}
      {report.preview.circumference && (report.preview.circumference.points.length ? <ChartCard title="Обхваты тела" subtitle="Талия и бёдра · сантиметры" option={circumferenceChartOption(report.preview.circumference.points)} ariaLabel="График талии и бёдер в пакете для врача" height={330} /> : <section className="report-empty panel"><h2>Обхваты тела</h2><p>За выбранный период данных нет.</p></section>)}
      {report.preview.pressure && (report.preview.pressure.points.length ? <ChartCard title="Давление" subtitle="Систолическое и диастолическое" option={pressureChartOption(report.preview.pressure.points)} ariaLabel="График давления в пакете для врача" height={330} /> : <section className="report-empty panel"><h2>Давление</h2><p>За выбранный период данных нет.</p></section>)}
      {report.preview.activity && (report.preview.activity.points.some((point) => point.steps !== null) ? <ChartCard title="Шаги · Xiaomi Cloud" subtitle="Health Connect не используется как подмена отсутствующих шагов" option={activityDailyChartOption(report.preview.activity.points)} ariaLabel="График шагов Xiaomi Cloud в пакете для врача" height={330} /> : <section className="report-empty panel"><h2>Шаги · Xiaomi Cloud</h2><p>Прямых данных Xiaomi Cloud за выбранный период нет. Значение не заменено нулём или данными Health Connect.</p></section>)}
      {report.preview.recovery && (report.preview.recovery.points.some((point) => point.sleepMinutes !== null) ? <ChartCard title="Сон" subtitle="Ось Y — часы; подсказки — часы и минуты" option={sleepChartOption(report.preview.recovery.points)} ariaLabel="График сна в часах в пакете для врача" height={330} /> : <section className="report-empty panel"><h2>Сон</h2><p>За выбранный период данных нет.</p></section>)}
      {report.preview.labs && <section className="report-list panel" aria-labelledby="report-labs-title"><div className="panel__head"><div><span className="eyebrow">Проверенные и исходные результаты</span><h2 id="report-labs-title">Лабораторные результаты</h2></div></div>{report.preview.labs.length ? <div className="data-table-scroll"><table className="data-table"><thead><tr><th>Дата</th><th>Показатель</th><th>Значение</th><th>Референс</th><th>Статус</th><th>Проверка</th></tr></thead><tbody>{report.preview.labs.map((item, index) => <tr key={`${item.analyte}-${item.observedOn}-${index}`}><td>{formatDate(item.observedOn)}</td><td>{item.analyte}</td><td>{item.value}</td><td>{item.reference ?? "—"}</td><td>{labStatusLabels[item.status]}</td><td>{item.verificationStatus === "unverified" ? "Не проверено" : item.verificationStatus === "corrected" ? "Исправлено" : "Проверено"}</td></tr>)}</tbody></table></div> : <p>За выбранный период результатов нет.</p>}</section>}
      {report.preview.studies && <section className="report-list panel" aria-labelledby="report-studies-title"><div className="panel__head"><div><span className="eyebrow">Только подтверждённые</span><h2 id="report-studies-title">Исследования</h2></div></div>{report.preview.studies.length ? report.preview.studies.map((item, index) => <article className="report-study" key={`${item.observedOn}-${item.modality}-${index}`}><strong>{formatDate(item.observedOn)} · {modalityLabels[item.modality]}</strong>{item.findings.length > 0 && <ul>{item.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul>}{item.conclusion && <p><b>Заключение:</b> {item.conclusion}</p>}</article>) : <p>За выбранный период подтверждённых исследований нет.</p>}</section>}
      {report.preview.ai && <section className="report-list panel" aria-labelledby="report-ai-title"><div className="panel__head"><div><span className="eyebrow">Добавлено по явному выбору</span><h2 id="report-ai-title">Валидированные AI-рекомендации</h2></div></div>{report.preview.ai.length ? report.preview.ai.map((item, index) => <article className="report-ai" key={`${item.title}-${index}`}><strong>{item.title}</strong><p>{item.text}</p></article>) : <p>Готовых рекомендаций нет.</p>}</section>}
    </article>}
  </>;
}
