import type { WeightRawPoint } from "../api/types";

export interface DailyWeightCandle {
  date: string;
  firstKg: number;
  lastKg: number;
  minimumKg: number;
  maximumKg: number;
  sampleCount: number;
}

const moscowDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit",
});

export function dailyWeightCandles(points: WeightRawPoint[]): DailyWeightCandle[] {
  const ordered = points
    .filter((point) => Number.isFinite(point.valueKg) && Number.isFinite(Date.parse(point.measuredAt)))
    .slice()
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
        date, firstKg: point.valueKg, lastKg: point.valueKg,
        minimumKg: point.valueKg, maximumKg: point.valueKg, sampleCount: 1,
      });
    }
  }
  return [...days.values()];
}

export function weightCandleChange(point: DailyWeightCandle): number | null {
  return point.sampleCount > 1 ? Number((point.lastKg - point.firstKg).toFixed(3)) : null;
}
