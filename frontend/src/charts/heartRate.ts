import type { HeartRateHourlyPoint } from "../api/types";

export const HEART_RATE_AGGREGATION_OPTIONS = [
  { hours: 1, label: "1 ч", description: "1 час" },
  { hours: 3, label: "3 ч", description: "3 часа" },
  { hours: 6, label: "6 ч", description: "6 часов" },
  { hours: 24, label: "1 день", description: "1 день" },
] as const;

export type HeartRateAggregationHours = typeof HEART_RATE_AGGREGATION_OPTIONS[number]["hours"];

const HOUR_MS = 60 * 60 * 1_000;
const MOSCOW_UTC_OFFSET_MS = 3 * HOUR_MS;

interface HeartRateBucket {
  measuredAt: string;
  sortTime: number;
  weightedSum: number;
  sampleCount: number;
  averageSum: number;
  averageCount: number;
  minimumBpm: number;
  maximumBpm: number;
}

function moscowBucketStart(measuredAt: string, hours: HeartRateAggregationHours): { key: string; measuredAt: string; sortTime: number } | null {
  const timestamp = Date.parse(measuredAt);
  if (!Number.isFinite(timestamp)) return null;
  const bucketSize = hours * HOUR_MS;
  // Bucket boundaries are always Moscow wall-clock boundaries, regardless of
  // whether the API timestamp is written as Z or with an explicit offset.
  const shiftedStart = Math.floor((timestamp + MOSCOW_UTC_OFFSET_MS) / bucketSize) * bucketSize;
  const sortTime = shiftedStart - MOSCOW_UTC_OFFSET_MS;
  const bucketMeasuredAt = `${new Date(shiftedStart).toISOString().slice(0, 19)}+03:00`;
  return { key: String(sortTime), measuredAt: bucketMeasuredAt, sortTime };
}

/**
 * Picks a readable initial resolution from the actual covered time span.
 * Users can still switch to any supported resolution in the chart controls.
 */
export function defaultHeartRateAggregation(points: HeartRateHourlyPoint[]): HeartRateAggregationHours {
  const timestamps = points
    .map((point) => Date.parse(point.measuredAt))
    .filter((value) => Number.isFinite(value));
  if (timestamps.length < 2) return 1;

  const bounds = timestamps.reduce(
    (result, value) => ({ minimum: Math.min(result.minimum, value), maximum: Math.max(result.maximum, value) }),
    { minimum: timestamps[0], maximum: timestamps[0] },
  );
  const spanHours = (bounds.maximum - bounds.minimum) / HOUR_MS;
  if (spanHours <= 3 * 24) return 1;
  if (spanHours <= 14 * 24) return 3;
  if (spanHours <= 45 * 24) return 6;
  return 24;
}

/**
 * Combines persisted hourly aggregates without reading or reconstructing raw
 * watch samples. Bounds stay true bounds and the mean is sample-count weighted.
 */
export function aggregateHeartRate(
  points: HeartRateHourlyPoint[],
  hours: HeartRateAggregationHours,
): HeartRateHourlyPoint[] {
  const buckets = new Map<string, HeartRateBucket>();

  for (const point of points) {
    if (![point.averageBpm, point.minimumBpm, point.maximumBpm].every(Number.isFinite)) continue;
    const start = moscowBucketStart(point.measuredAt, hours);
    if (!start) continue;
    const weight = Number.isFinite(point.sampleCount) && point.sampleCount > 0 ? point.sampleCount : 0;
    const bucket = buckets.get(start.key) ?? {
      measuredAt: start.measuredAt,
      sortTime: start.sortTime,
      weightedSum: 0,
      sampleCount: 0,
      averageSum: 0,
      averageCount: 0,
      minimumBpm: point.minimumBpm,
      maximumBpm: point.maximumBpm,
    };
    bucket.weightedSum += point.averageBpm * weight;
    bucket.sampleCount += weight;
    bucket.averageSum += point.averageBpm;
    bucket.averageCount += 1;
    bucket.minimumBpm = Math.min(bucket.minimumBpm, point.minimumBpm);
    bucket.maximumBpm = Math.max(bucket.maximumBpm, point.maximumBpm);
    buckets.set(start.key, bucket);
  }

  return [...buckets.values()]
    .sort((left, right) => left.sortTime - right.sortTime)
    .map((bucket) => ({
      measuredAt: bucket.measuredAt,
      averageBpm: Math.round((bucket.sampleCount > 0
        ? bucket.weightedSum / bucket.sampleCount
        : bucket.averageSum / bucket.averageCount) * 10) / 10,
      minimumBpm: bucket.minimumBpm,
      maximumBpm: bucket.maximumBpm,
      sampleCount: bucket.sampleCount,
    }));
}

/** Inserts an explicit null whenever at least one complete bucket is missing. */
export function heartRateLineData(
  points: HeartRateHourlyPoint[],
  selector: (point: HeartRateHourlyPoint) => number,
  hours: HeartRateAggregationHours,
): Array<[string, number | null]> {
  const sorted = [...points].sort((left, right) => Date.parse(left.measuredAt) - Date.parse(right.measuredAt));
  const result: Array<[string, number | null]> = [];
  const expectedInterval = hours * HOUR_MS;

  sorted.forEach((point, index) => {
    if (index > 0) {
      const previousTime = Date.parse(sorted[index - 1].measuredAt);
      const currentTime = Date.parse(point.measuredAt);
      if (Number.isFinite(previousTime) && Number.isFinite(currentTime) && currentTime - previousTime > expectedInterval * 1.5) {
        result.push([new Date((previousTime + currentTime) / 2).toISOString(), null]);
      }
    }
    result.push([point.measuredAt, selector(point)]);
  });

  return result;
}
