import { normalizeCompositionSeries, normalizeOverview, normalizePressureSeries, normalizeWeightSeries } from "./client";

describe("API normalization", () => {
  it("normalizes the canonical overview contract", () => {
    const result = normalizeOverview({
      generated_at: "2026-08-19T08:00:00Z",
      plan: { start_date: "2026-08-15", start_weight_kg: 127.03, target_weight_kg: 76.5, planned_today_kg: 126.5 },
      weight: { latest_kg: 126.2, latest_at: "2026-08-19T05:00:00Z", progress_pct: 1.6 },
      sync: { status: "ok", last_success_at: "2026-08-19T08:00:00Z" },
      insights: [{ id: "rule-1", title: "Ритм", text: "Продолжайте измерения", tone: "positive" }],
    });
    expect(result.weight.latestKg).toBe(126.2);
    expect(result.plan.plannedTodayKg).toBe(126.5);
    expect(result.sync.status).toBe("ok");
    expect(result.insights[0].text).toBe("Продолжайте измерения");
  });

  it("does not hide an explicitly delayed sync after initial import", () => {
    const result = normalizeOverview({
      sync: { status: "delayed", initial_import_done: true },
    });
    expect(result.sync.status).toBe("delayed");
  });

  it("supports the legacy daily weight shape", () => {
    const result = normalizeWeightSeries({
      range: "program",
      daily: [{ date: "2026-08-18", value: 126.8, sample_count: 2, rolling_7d: 126.9, planned: 126.64, is_outlier: false }],
      raw: [{ measured_at: "2026-08-18T05:00:00Z", value: 126.7 }],
    }, "program");
    expect(result.points).toHaveLength(1);
    expect(result.points[0]).toMatchObject({ weightKg: 126.8, smoothed7dKg: 126.9, plannedKg: 126.64 });
    expect(result.meta.range).toBe("program");
    expect(result.projection).toEqual([]);
    expect(result.planProjection).toEqual([]);
  });

  it("normalizes independent forecast and plan projections", () => {
    const result = normalizeWeightSeries({
      points: [{ measured_at: "2026-09-01", weight_kg: 124 }],
      projection: [{ measured_at: "2027-08-01", forecast_kg: 76.5, forecast_low_kg: 76.5, forecast_high_kg: 77.2 }],
      plan_projection: [{ measured_at: "2026-09-01", planned_kg: 124.84 }],
    }, "program");
    expect(result.projection[0].forecastKg).toBe(76.5);
    expect(result.planProjection[0].plannedKg).toBe(124.84);
  });

  it("supports sessions and nested pressure statistics", () => {
    const result = normalizePressureSeries({
      range: "30d",
      sessions: [{ measured_at: "2026-08-19T05:00:00Z", systolic: 122, diastolic: 78, pulse: 64, sample_count: 3 }],
      statistics: { last_7_days: { sessions: 1, systolic: { mean: 122, min: 122, max: 122, variability: 0 }, diastolic: { mean: 78, min: 78, max: 78, variability: 0 }, pulse: { mean: 64 } } },
    }, "30d");
    expect(result.points[0].sessionSize).toBe(3);
    expect(result.stats7d.avgSystolic).toBe(122);
    expect(result.stats7d.avgPulse).toBe(64);
  });

  it("joins legacy composition measurements by timestamp", () => {
    const result = normalizeCompositionSeries({
      range: "all",
      series: {
        fat_percent: [{ measured_at: "2026-08-19T05:00:00Z", value: 31.2 }],
        fat_mass: [{ measured_at: "2026-08-19T05:00:00Z", value: 39.4 }],
        fat_free_mass: [{ measured_at: "2026-08-19T05:00:00Z", value: 86.8 }],
      },
    }, "all");
    expect(result.points[0]).toMatchObject({ fatPct: 31.2, fatMassKg: 39.4, leanMassKg: 86.8 });
  });
});
