import type { Period } from "../api/types";

const numberFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const integerFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (digits === 0) return integerFormatter.format(value);
  return numberFormatter.format(value);
}

export function formatKg(value: number | null | undefined): string {
  const formatted = formatNumber(value);
  return formatted === "—" ? formatted : `${formatted} кг`;
}

export function formatMmhg(value: number | null | undefined): string {
  const formatted = formatNumber(value, 0);
  return formatted === "—" ? formatted : `${formatted} мм рт. ст.`;
}

export function formatDelta(value: number | null | undefined, unit = "кг"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(value))} ${unit}`;
}

export function formatPercent(value: number | null | undefined): string {
  const formatted = formatNumber(value, 0);
  return formatted === "—" ? formatted : `${formatted}%`;
}

function validDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(value: string | null | undefined, withYear = true): string {
  const date = validDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
    timeZone: "Europe/Moscow",
  })
    .format(date)
    .replace(" г.", "");
}

export function formatDateTime(value: string | null | undefined): string {
  const date = validDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Moscow",
  }).format(date);
}

export function formatShortDate(value: string): string {
  const date = validDate(value);
  if (!date) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    timeZone: "Europe/Moscow",
  }).format(date);
}

export function relativeTime(value: string | null | undefined): string {
  const date = validDate(value);
  if (!date) return "время неизвестно";
  const deltaMinutes = Math.round((date.getTime() - Date.now()) / 60_000);
  if (Math.abs(deltaMinutes) < 1) return "только что";
  const formatter = new Intl.RelativeTimeFormat("ru-RU", { numeric: "auto" });
  if (Math.abs(deltaMinutes) < 60) return formatter.format(deltaMinutes, "minute");
  const deltaHours = Math.round(deltaMinutes / 60);
  if (Math.abs(deltaHours) < 48) return formatter.format(deltaHours, "hour");
  return formatter.format(Math.round(deltaHours / 24), "day");
}

export const periodLabels: Record<Period, string> = {
  program: "С начала плана",
  "30d": "30 дней",
  "90d": "90 дней",
  "1y": "Год",
  all: "Всё время",
};

export function clampProgress(value: number | null): number {
  if (value === null || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}
