import type { HeartRateHourlyPoint } from "../api/types";
import {
  aggregateHeartRate,
  defaultHeartRateAggregation,
  heartRateLineData,
  type HeartRateAggregationHours,
} from "./heartRate";

function point(
  measuredAt: string,
  overrides: Partial<HeartRateHourlyPoint> = {},
): HeartRateHourlyPoint {
  return {
    measuredAt,
    averageBpm: 70,
    minimumBpm: 55,
    maximumBpm: 90,
    sampleCount: 1,
    ...overrides,
  };
}

describe("watch heart-rate aggregation", () => {
  it("keeps true bounds and weights the average by sample count", () => {
    const result = aggregateHeartRate([
      point("2026-08-20T09:00:00+03:00", { averageBpm: 60, minimumBpm: 50, maximumBpm: 72, sampleCount: 2 }),
      point("2026-08-20T10:00:00+03:00", { averageBpm: 90, minimumBpm: 65, maximumBpm: 110, sampleCount: 6 }),
      point("2026-08-20T13:00:00+03:00", { averageBpm: 75, minimumBpm: 58, maximumBpm: 95, sampleCount: 3 }),
    ], 3);

    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({
      measuredAt: "2026-08-20T09:00:00+03:00",
      averageBpm: 82.5,
      minimumBpm: 50,
      maximumBpm: 110,
      sampleCount: 8,
    });
    expect(result[1]).toMatchObject({
      measuredAt: "2026-08-20T12:00:00+03:00",
      averageBpm: 75,
      minimumBpm: 58,
      maximumBpm: 95,
    });
  });

  it("uses local calendar-day boundaries for daily aggregation", () => {
    const result = aggregateHeartRate([
      point("2026-08-20T23:00:00+03:00", { averageBpm: 80 }),
      point("2026-08-21T00:00:00+03:00", { averageBpm: 60 }),
    ], 24);

    expect(result.map((item) => item.measuredAt)).toEqual([
      "2026-08-20T00:00:00+03:00",
      "2026-08-21T00:00:00+03:00",
    ]);
  });

  it("aligns Z timestamps to Moscow buckets instead of UTC buckets", () => {
    const result = aggregateHeartRate([
      point("2026-08-20T21:30:00Z", { averageBpm: 80 }),
    ], 24);

    expect(result[0].measuredAt).toBe("2026-08-21T00:00:00+03:00");
  });

  it.each([
    [2, 1],
    [7, 3],
    [30, 6],
    [60, 24],
  ] as Array<[number, HeartRateAggregationHours]>)
  ("defaults a %s-day range to %s-hour buckets", (spanDays, expected) => {
    expect(defaultHeartRateAggregation([
      point("2026-06-01T00:00:00+03:00"),
      point(new Date(Date.parse("2026-06-01T00:00:00+03:00") + spanDays * 86_400_000).toISOString()),
    ])).toBe(expected);
  });

  it("inserts a null break instead of connecting across missing buckets", () => {
    const points = [
      point("2026-08-05T18:00:00+03:00"),
      point("2026-08-06T00:00:00+03:00"),
      point("2026-08-14T06:00:00+03:00"),
    ];

    const line = heartRateLineData(points, (item) => item.averageBpm, 6);

    expect(line).toHaveLength(4);
    expect(line[0][1]).toBe(70);
    expect(line[1][1]).toBe(70);
    expect(line[2][1]).toBeNull();
    expect(line[3]).toEqual(["2026-08-14T06:00:00+03:00", 70]);
  });
});
