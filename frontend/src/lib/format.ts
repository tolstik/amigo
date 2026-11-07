import type { Period } from "../api/types";

const numberFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const integerFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (digits === 0) return integerFormatter.format(value);
  if (digits !== 1) return new Intl.NumberFormat("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  return numberFormatter.format(value);
}

export function formatKg(value: number | null | undefined, digits = 1): string {
  const formatted = formatNumber(value, digits);
  return formatted === "—" ? formatted : `${formatted} кг`;
}

export function formatMmhg(value: number | null | undefined): string {
  const formatted = formatNumber(value, 0);
  return formatted === "—" ? formatted : `${formatted} мм рт. ст.`;
}

export function formatDelta(value: number | null | undefined, unit = "кг", digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(value), digits)} ${unit}`;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  const formatted = formatNumber(value, digits);
  return formatted === "—" ? formatted : `${formatted}%`;
}

function validDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // Date-only API values represent a Moscow calendar day, not UTC midnight
  // (which would render as the previous day in the dashboard timezone).
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T12:00:00Z`)
    : new Date(value);
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

export function formatDateTime(value: string | null | undefined, withYear = false): string {
  const date = validDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    ...(withYear ? { year: "numeric" } : {}),
    timeZone: "Europe/Moscow",
  }).format(date);
}

export function formatMonth(value: string): string {
  const date = validDate(value);
  if (!date) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    month: "short",
    year: "numeric",
    timeZone: "Europe/Moscow",
  }).format(date).replace(" г.", "");
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

function moscowParts(now: Date): { hour: number; date: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
    timeZone: "Europe/Moscow",
  }).formatToParts(now);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return { hour: Number(get("hour")), date: `${get("year")}-${get("month")}-${get("day")}` };
}

/** Return today's Moscow calendar date as YYYY-MM-DD. */
export function moscowToday(now: Date = new Date()): string {
  return moscowParts(now).date;
}

/** Shift a YYYY-MM-DD calendar date by whole days without timezone drift. */
export function shiftIsoDate(value: string, days: number): string {
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** Greeting that follows the Moscow clock rather than the browser timezone. */
export function greeting(now: Date = new Date()): string {
  const { hour } = moscowParts(now);
  if (hour >= 5 && hour < 12) return "Доброе утро";
  if (hour >= 12 && hour < 18) return "Добрый день";
  if (hour >= 18 && hour < 23) return "Добрый вечер";
  return "Доброй ночи";
}
