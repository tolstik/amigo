import type { WeightRawPoint } from "../api/types";

export interface DailyWeightCandle {
  date: string;
  previousKg: number | null;
  previousDate: string | null;
  lastKg: number;
  minimumKg: number;
  maximumKg: number;
  sampleCount: number;
  comparisonLabel?: string;
}

export interface DailyWeightCandleOptions {
  startAt?: string;
  baselineKg?: number;
  baselineDate?: string;
}

const moscowDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit",
});

export const WEIGHT_CANDLE_DAYS = 90;

export function weightCandleDates(asOf: string): string[] {
  const today = Date.parse(moscowDay.format(new Date(asOf)));
  return Array.from({ length: WEIGHT_CANDLE_DAYS }, (_, index) => new Date(today - (WEIGHT_CANDLE_DAYS - 1 - index) * 86_400_000).toISOString().slice(0, 10));
}

export function dailyWeightCandles(
  points: WeightRawPoint[],
  asOf: string,
  options: DailyWeightCandleOptions = {},
): DailyWeightCandle[] {
  const startAt = options.startAt ? Date.parse(options.startAt) : Number.NEGATIVE_INFINITY;
  const ordered = points
    .filter((point) => {
      const measuredAt = Date.parse(point.measuredAt);
      return Number.isFinite(point.valueKg) && Number.isFinite(measuredAt) && measuredAt >= startAt;
    })
    .sort((left, right) => Date.parse(left.measuredAt) - Date.parse(right.measuredAt));
  const days = new Map<string, DailyWeightCandle>();
  for (const point of ordered) {
    const date = moscowDay.format(new Date(point.measuredAt));
    // User-requested exclusion for this chart, including subsequent comparisons.
    if (date === "2026-07-31") continue;
    const day = days.get(date);
    if (day) {
      day.lastKg = point.valueKg;
      day.minimumKg = Math.min(day.minimumKg, point.valueKg);
      day.maximumKg = Math.max(day.maximumKg, point.valueKg);
      day.sampleCount += 1;
    } else {
      days.set(date, {
        date, previousKg: null, previousDate: null, lastKg: point.valueKg,
        minimumKg: point.valueKg, maximumKg: point.valueKg, sampleCount: 1,
      });
    }
  }
  const orderedDays = [...days.values()];
  // Establish the previous measured day's close before trimming the visible period.
  // One weighing per day must still produce a body spanning the entire change.
  orderedDays.forEach((day, index) => {
    const previous = orderedDays[index - 1];
    day.previousKg = previous?.lastKg ?? null;
    day.previousDate = previous?.date ?? null;
    if (index === 0 && Number.isFinite(options.baselineKg)) {
      day.previousKg = options.baselineKg!;
      day.previousDate = options.baselineDate ?? null;
      day.comparisonLabel = "Старт программы";
    }
  });
  const dates = weightCandleDates(asOf);
  return orderedDays.filter((day) => day.date >= dates[0] && day.date <= dates[WEIGHT_CANDLE_DAYS - 1]);
}

export function weightCandleChange(point: DailyWeightCandle): number | null {
  return point.previousKg !== null ? Number((point.lastKg - point.previousKg).toFixed(3)) : null;
}
