import type { WeightRawPoint } from "../api/types";

export interface DailyWeightCandle {
  date: string;
  previousKg: number | null;
  previousDate: string | null;
  lastKg: number;
  minimumKg: number;
  maximumKg: number;
  sampleCount: number;
}

const moscowDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit",
});

export function weightCandleDates(asOf: string): string[] {
  const today = Date.parse(moscowDay.format(new Date(asOf)));
  return Array.from({ length: 7 }, (_, index) => new Date(today - (6 - index) * 86_400_000).toISOString().slice(0, 10));
}

export function dailyWeightCandles(points: WeightRawPoint[], asOf: string): DailyWeightCandle[] {
  const ordered = points
    .filter((point) => Number.isFinite(point.valueKg) && Number.isFinite(Date.parse(point.measuredAt)))
    .sort((left, right) => Date.parse(left.measuredAt) - Date.parse(right.measuredAt));
  const days = new Map<string, DailyWeightCandle>();
  for (const point of ordered) {
    const date = moscowDay.format(new Date(point.measuredAt));
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
  // Establish the previous measured day's close before trimming the visible week.
  // One weighing per day must still produce a body spanning the entire change.
  orderedDays.forEach((day, index) => {
    const previous = orderedDays[index - 1];
    day.previousKg = previous?.lastKg ?? null;
    day.previousDate = previous?.date ?? null;
  });
  const dates = weightCandleDates(asOf);
  return orderedDays.filter((day) => day.date >= dates[0] && day.date <= dates[6]);
}

export function weightCandleChange(point: DailyWeightCandle): number | null {
  return point.previousKg !== null ? Number((point.lastKg - point.previousKg).toFixed(3)) : null;
}
