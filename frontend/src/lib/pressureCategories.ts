import type { PressurePoint } from "../api/types";

export type PressureCategory = "below_guide" | "home_guide" | "elevated" | "critical_high";

interface PressureCategoryDefinition {
  label: string;
  boundary: string;
  rank: number;
}

export const PRESSURE_CATEGORY_LEGEND_ORDER: PressureCategory[] = [
  "below_guide",
  "home_guide",
  "elevated",
  "critical_high",
];

export const PRESSURE_CATEGORY_DEFINITIONS: Record<PressureCategory, PressureCategoryDefinition> = {
  below_guide: {
    label: "Ниже ориентира",
    boundary: "сист. < 90 или диаст. < 60",
    rank: 1,
  },
  home_guide: {
    label: "Домашний ориентир",
    boundary: "сист. 90–134 и диаст. 60–84",
    rank: 0,
  },
  elevated: {
    label: "Повышенное",
    boundary: "сист. 135–179 или диаст. 85–119",
    rank: 2,
  },
  critical_high: {
    label: "Критически высокое",
    boundary: "сист. ≥ 180 или диаст. ≥ 120",
    rank: 3,
  },
};

export interface DailyPressureCategory {
  date: string;
  category: PressureCategory;
  sessions: number;
  minSystolic: number;
  maxSystolic: number;
  minDiastolic: number;
  maxDiastolic: number;
}

export function classifyPressureCategory(systolic: number, diastolic: number): PressureCategory {
  if (systolic >= 180 || diastolic >= 120) return "critical_high";
  if (systolic >= 135 || diastolic >= 85) return "elevated";
  if (systolic < 90 || diastolic < 60) return "below_guide";
  return "home_guide";
}

function localDateKey(measuredAt: string, timeZone: string): string | null {
  const date = new Date(measuredAt);
  if (!Number.isFinite(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value;
  const year = value("year");
  const month = value("month");
  const day = value("day");
  return year && month && day ? `${year}-${month}-${day}` : null;
}

export function aggregateDailyPressureCategories(
  points: PressurePoint[],
  timeZone = "Europe/Moscow",
): DailyPressureCategory[] {
  const days = new Map<string, DailyPressureCategory>();
  points.forEach((point) => {
    const date = localDateKey(point.measuredAt, timeZone);
    if (!date) return;
    const category = classifyPressureCategory(point.systolic, point.diastolic);
    const current = days.get(date);
    if (!current) {
      days.set(date, {
        date,
        category,
        sessions: 1,
        minSystolic: point.systolic,
        maxSystolic: point.systolic,
        minDiastolic: point.diastolic,
        maxDiastolic: point.diastolic,
      });
      return;
    }
    current.sessions += 1;
    current.minSystolic = Math.min(current.minSystolic, point.systolic);
    current.maxSystolic = Math.max(current.maxSystolic, point.systolic);
    current.minDiastolic = Math.min(current.minDiastolic, point.diastolic);
    current.maxDiastolic = Math.max(current.maxDiastolic, point.diastolic);
    if (
      PRESSURE_CATEGORY_DEFINITIONS[category].rank >
      PRESSURE_CATEGORY_DEFINITIONS[current.category].rank
    ) {
      current.category = category;
    }
  });
  return [...days.values()].sort((left, right) => left.date.localeCompare(right.date));
}
