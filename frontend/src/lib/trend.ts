import { moscowToday, shiftIsoDate } from "./format";

/**
 * Align chronological points to the last `days` Moscow calendar days.
 * Missing days stay null so sparklines never interpolate across gaps.
 */
export function dailyTrend<T>(
  points: readonly T[],
  dateOf: (point: T) => string | null | undefined,
  valueOf: (point: T) => number | null | undefined,
  days: number,
  today: string = moscowToday(),
): Array<number | null> {
  const byDay = new Map<string, number>();
  for (const point of points) {
    const day = dateOf(point)?.slice(0, 10);
    const value = valueOf(point);
    if (day && value !== null && value !== undefined && Number.isFinite(value)) byDay.set(day, value);
  }
  const start = shiftIsoDate(today, -(days - 1));
  return Array.from({ length: days }, (_, index) => byDay.get(shiftIsoDate(start, index)) ?? null);
}

export function hasTrend(values: Array<number | null>): boolean {
  return values.filter((value) => value !== null).length >= 2;
}
