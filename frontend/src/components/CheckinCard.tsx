import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DailyCheckin } from "../api/types";
import { checkinFields, type CheckinField } from "../lib/journal";
import { formatDate } from "../lib/format";

type Values = Record<CheckinField, number | null>;

const empty: Values = { energy: null, hunger: null, mood: null };

/** One-tap 1–5 self-assessment for a single Moscow day. Each tap saves immediately. */
export function CheckinCard({ day, initial, onSaved, compact = false }: {
  day: string;
  initial: DailyCheckin | null;
  onSaved?: () => void;
  compact?: boolean;
}) {
  const [values, setValues] = useState<Values>(initial ?? empty);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useEffect(() => { setValues(initial ? { energy: initial.energy, hunger: initial.hunger, mood: initial.mood } : empty); }, [initial, day]);

  async function choose(field: CheckinField, value: number) {
    const previous = values;
    const next = { ...values, [field]: values[field] === value ? null : value };
    setValues(next);
    setStatus("saving");
    try {
      if (next.energy === null && next.hunger === null && next.mood === null) {
        if (previous.energy !== null || previous.hunger !== null || previous.mood !== null) await api.deleteCheckin(day);
      } else {
        await api.saveCheckin(day, next);
      }
      setStatus("saved");
      onSaved?.();
    } catch {
      setValues(previous);
      setStatus("error");
    }
  }

  return (
    <section className={`panel checkin-card${compact ? " checkin-card--compact" : ""}`} aria-labelledby={`checkin-${day}`}>
      <div className="panel__head">
        <div>
          <span className="eyebrow">Самооценка · {formatDate(day)}</span>
          <h2 id={`checkin-${day}`}>Как вы сегодня?</h2>
        </div>
        <span className={`checkin-card__status checkin-card__status--${status}`} role="status" aria-live="polite">
          {status === "saving" ? "Сохраняем…" : status === "saved" ? "Сохранено" : status === "error" ? "Не удалось сохранить" : "Одно касание — сохранено"}
        </span>
      </div>
      <div className="checkin-card__fields">
        {checkinFields.map((field) => (
          <div className="checkin-field" key={field.key} role="group" aria-label={`${field.label}: от 1 (${field.low}) до 5 (${field.high})`}>
            <span className="checkin-field__label">{field.label}</span>
            <div className="checkin-scale">
              {[1, 2, 3, 4, 5].map((value) => (
                <button
                  key={value}
                  type="button"
                  className={values[field.key] === value ? "is-active" : undefined}
                  aria-pressed={values[field.key] === value}
                  aria-label={`${field.label} ${value} из 5`}
                  disabled={status === "saving"}
                  onClick={() => choose(field.key, value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <span className="checkin-field__hint"><span>{field.low}</span><span>{field.high}</span></span>
          </div>
        ))}
      </div>
    </section>
  );
}
