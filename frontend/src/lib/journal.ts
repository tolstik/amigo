import type { NoteCategory } from "../api/types";

export const noteCategoryLabels: Record<NoteCategory, string> = {
  illness: "Болезнь",
  holiday: "Праздник",
  travel: "Поездка",
  medication: "Препарат",
  stress: "Стресс",
  other: "Другое",
};

export const checkinFields = [
  { key: "energy", label: "Энергия", low: "Нет сил", high: "Бодрость" },
  { key: "hunger", label: "Голод", low: "Сыт", high: "Очень голоден" },
  { key: "mood", label: "Настроение", low: "Плохое", high: "Отличное" },
] as const;

export type CheckinField = (typeof checkinFields)[number]["key"];

export const checkinMetricLabels: Record<string, string> = {
  energy: "Энергия",
  hunger: "Голод",
  mood: "Настроение",
  sleep_minutes: "Сон",
  steps: "Шаги",
};
