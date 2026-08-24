import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { EvidenceDescriptor, EvidenceMap } from "../api/types";
import { formatDate, formatNumber } from "../lib/format";

function duration(minutes: number): string {
  const rounded = Math.max(0, Math.round(minutes));
  return `${Math.floor(rounded / 60)} ч ${rounded % 60} мин`;
}

function evidenceValue(item: EvidenceDescriptor): string | null {
  const raw = item.value ?? item.text;
  if (raw === null) return null;
  if (typeof raw === "number" && item.metric === "sleep" && /min|мин/i.test(item.unit ?? "")) {
    return duration(raw);
  }
  const value = typeof raw === "number" ? formatNumber(raw, 2) : String(raw);
  return `${item.comparator && item.comparator !== "=" ? item.comparator : ""}${value}${item.unit ? ` ${item.unit}` : ""}`;
}

function reference(item: EvidenceDescriptor): string | null {
  if (item.referenceText) return item.referenceText;
  if (item.referenceLow !== null && item.referenceHigh !== null) return `${formatNumber(item.referenceLow)}–${formatNumber(item.referenceHigh)}${item.unit ? ` ${item.unit}` : ""}`;
  if (item.referenceLow !== null) return `от ${formatNumber(item.referenceLow)}${item.unit ? ` ${item.unit}` : ""}`;
  if (item.referenceHigh !== null) return `до ${formatNumber(item.referenceHigh)}${item.unit ? ` ${item.unit}` : ""}`;
  return null;
}

function EvidenceDrawer({ item, onClose }: { item: EvidenceDescriptor | null; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  const value = item ? evidenceValue(item) : null;
  const referenceValue = item ? reference(item) : null;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title">
        <div className="evidence-drawer__head">
          <div><span className="eyebrow">Основание вывода</span><h2 id="evidence-drawer-title">{item?.label ?? "Описание недоступно"}</h2></div>
          <button ref={closeButton} type="button" className="icon-button" onClick={onClose} aria-label="Закрыть основание">×</button>
        </div>
        <p className="evidence-drawer__immutability">Это значение зафиксировано в момент анализа и не меняется задним числом.</p>
        {item ? <dl className="evidence-details">
          {value && <><dt>Значение</dt><dd>{value}</dd></>}
          {item.observedOn && <><dt>Дата</dt><dd>{formatDate(item.observedOn)}</dd></>}
          {(item.rangeStart || item.rangeEnd) && <><dt>Период</dt><dd>{item.rangeStart ? formatDate(item.rangeStart) : "…"} — {item.rangeEnd ? formatDate(item.rangeEnd) : "…"}</dd></>}
          {item.count !== null && <><dt>Точек данных</dt><dd>{formatNumber(item.count, 0)}</dd></>}
          {referenceValue && <><dt>Референс</dt><dd>{referenceValue}</dd></>}
          {item.referenceStatus && <><dt>Статус</dt><dd>{item.referenceStatus}</dd></>}
          {item.verification && <><dt>Проверка</dt><dd>{item.verification === "verified" ? "Подтверждено" : "Не подтверждено"}</dd></>}
        </dl> : <p className="panel-note">Метаданные этого основания недоступны в сохранённом результате.</p>}
        {item?.target.available && item.target.path
          ? <Link className="button button--secondary" to={item.target.path} onClick={onClose}>Открыть исходные данные</Link>
          : item && <p className="panel-note">Исходная запись больше недоступна, но зафиксированное основание сохранено.</p>}
      </section>
    </div>
  );
}

export function EvidenceChips({ evidenceIds, evidence }: { evidenceIds: string[]; evidence: EvidenceMap }) {
  const ids = [...new Set(evidenceIds)];
  const [activeId, setActiveId] = useState<string | null>(null);
  if (!ids.length) return null;
  return <>
    <div className="evidence-chips" aria-label="Основания вывода">
      {ids.map((id, index) => <button key={id} type="button" className="evidence-chip" onClick={() => setActiveId(id)}>
        {evidence[id]?.label ?? `Основание ${index + 1}`}
      </button>)}
    </div>
    {activeId && <EvidenceDrawer item={evidence[activeId] ?? null} onClose={() => setActiveId(null)} />}
  </>;
}
