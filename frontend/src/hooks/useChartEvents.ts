import { useCallback, useMemo } from "react";
import { api } from "../api/client";
import type { ChartEvent } from "../charts/options";
import { noteCategoryLabels } from "../lib/journal";
import { useApi } from "./useApi";

/**
 * Load owner-entered chart annotations. Failures are silent: markers are an
 * optional overlay and must never block a measurement chart from rendering.
 */
export function useChartEvents({ medications = false }: { medications?: boolean } = {}): ChartEvent[] {
  const loadNotes = useCallback((signal: AbortSignal) => api.notes("all", signal).catch(() => []), []);
  const notes = useApi(loadNotes);
  const loadMedications = useCallback(
    (signal: AbortSignal) => (medications ? api.medications(signal).catch(() => []) : Promise.resolve([])),
    [medications],
  );
  const meds = useApi(loadMedications);
  return useMemo(() => {
    const events: ChartEvent[] = (notes.data ?? []).map((note) => ({
      day: note.day,
      kind: "note",
      label: `${noteCategoryLabels[note.category]}: ${note.text.length > 60 ? `${note.text.slice(0, 57)}…` : note.text}`,
    }));
    for (const medication of meds.data ?? []) {
      if (medication.started_on) events.push({ day: medication.started_on, kind: "medication", label: `Начало: ${medication.name}` });
      if (medication.stopped_on) events.push({ day: medication.stopped_on, kind: "medication", label: `Окончание: ${medication.name}` });
    }
    return events;
  }, [notes.data, meds.data]);
}
