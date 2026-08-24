import {
  normalizeActivitySeries,
  normalizeAiAnalysis,
  normalizeAssistantMessages,
  normalizeCompositionSeries,
  normalizeDataQuality,
  normalizeDoctorReport,
  normalizeHealthTaskList,
  normalizeLabCompare,
  normalizeOverview,
  normalizePressureSeries,
  normalizeRecoverySeries,
  normalizeWeightSeries,
} from "./client";

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
    expect(result.weekly).toEqual([]);
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

  it("normalizes continuous weekly plan and fact buckets including gaps", () => {
    const result = normalizeWeightSeries({
      points: [{ measured_at: "2026-08-31", weight_kg: 125.5 }],
      weekly: [
        {
          start_date: "2026-08-24",
          end_date: "2026-08-30",
          actual_avg_kg: null,
          actual_min_kg: null,
          planned_avg_kg: 125.7,
          actual_change_kg: null,
          planned_change_kg: -0.9,
          deviation_from_plan_kg: null,
          measurement_days: 0,
          sample_count: 0,
          outlier_days: 0,
          is_partial: false,
        },
        {
          start_date: "2026-08-17",
          end_date: "2026-08-23",
          actual_avg_kg: "126,25",
          actual_min_kg: 126,
          planned_avg_kg: 126.6,
          actual_change_kg: -0.75,
          planned_change_kg: -0.43,
          deviation_from_plan_kg: -0.35,
          measurement_days: 3,
          sample_count: 4,
          outlier_days: 1,
          is_partial: false,
        },
      ],
    }, "program");

    expect(result.weekly).toHaveLength(2);
    expect(result.weekly[0]).toMatchObject({
      startDate: "2026-08-17",
      actualAvgKg: 126.25,
      actualChangeKg: -0.75,
      measurementDays: 3,
      sampleCount: 4,
      outlierDays: 1,
      isPartial: false,
    });
    expect(result.weekly[1]).toMatchObject({
      startDate: "2026-08-24",
      actualAvgKg: null,
      actualChangeKg: null,
      plannedChangeKg: -0.9,
      measurementDays: 0,
    });
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

  it("normalizes activity days, weekly baseline and freshness", () => {
    const result = normalizeActivitySeries({
      points: [{ date: "2026-08-18", steps: 8432, distance_km: 6.2, active_minutes: 47 }],
      weekly: [{ start_date: "2026-08-17", end_date: "2026-08-23", actual_steps: 42000, baseline_steps: 39000, coverage_days: 4, is_partial: true }],
      summary: { data_as_of: "2026-08-18T20:00:00Z", baseline_steps: 7900 },
      correlations: [{ metric: "steps", target: "weight_kg", coefficient: -0.42, full_overlapping_weeks: 9, disclaimer: "Корреляция не доказывает причинность." }],
    }, "30d");
    expect(result.points[0]).toMatchObject({ steps: 8432, distanceKm: 6.2, activeMinutes: 47 });
    expect(result.weekly[0]).toMatchObject({ actualSteps: 42000, baselineSteps: 39000, isPartial: true });
    expect(result.summary.dataAsOf).toBe("2026-08-18T20:00:00Z");
    expect(result.correlations[0]).toMatchObject({ metric: "steps", coefficient: -0.42, fullOverlappingWeeks: 9 });
  });

  it("keeps recovery metrics optional", () => {
    const result = normalizeRecoverySeries({
      points: [{
        date: "2026-08-18",
        sleep_minutes: 438,
        average_heart_rate_bpm: 59,
        minimum_heart_rate_bpm: 47,
        maximum_heart_rate_bpm: 89,
        resting_heart_rate_bpm: 62,
      }],
      heart_rate_hourly: [{
        measured_at: "2026-08-18T12:00:00+03:00",
        average_bpm: 61,
        minimum_bpm: 52,
        maximum_bpm: 76,
        sample_count: 18,
      }],
      available_metrics: ["heart_rate", "sleep", "resting_heart_rate"],
      correlations: [{ metric: "sleep_minutes", target: "weight_kg", coefficient: 0.31, full_overlapping_weeks: 8 }],
    }, "90d");
    expect(result.points[0]).toMatchObject({
      sleepMinutes: 438,
      averageHeartRateBpm: 59,
      minimumHeartRateBpm: 47,
      maximumHeartRateBpm: 89,
      restingHeartRateBpm: 62,
      hrvRmssdMs: null,
    });
    expect(result.summary).toMatchObject({ averageHeartRateBpm: 59, minimumHeartRateBpm: 47, maximumHeartRateBpm: 89 });
    expect(result.heartRateHourly).toEqual([{
      measuredAt: "2026-08-18T12:00:00+03:00",
      averageBpm: 61,
      minimumBpm: 52,
      maximumBpm: 76,
      sampleCount: 18,
    }]);
    expect(result.availableMetrics).toEqual(["heart_rate", "sleep", "resting_heart_rate"]);
    expect(result.correlations[0].disclaimer).toBe("Корреляция не доказывает причинность.");
  });

  it("normalizes schema-validated AI analysis without template fields", () => {
    const result = normalizeAiAnalysis({
      analysis_id: 42,
      status: "fresh",
      headline: "Темп устойчив",
      summary: "Активность выросла относительно личной базы.",
      insights: [{ id: "i1", title: "Активность", text: "Неделя активнее обычного.", evidence_ids: ["activity.week"] }],
      recommendations: [{ id: "r1", title: "Следующий шаг", text: "Сохраните текущий ритм.", evidence_ids: ["activity.week"] }],
      limitations: ["Неполная неделя"],
      generated_at: "2026-08-19T06:00:00Z",
      model: "gpt-5.6-sol",
      evidence: {
        "activity.week": { key: "activity.week", kind: "series", metric: "activity", label: "Активность", unit: "steps", range: { from: "2026-08-12", to: "2026-08-19" }, count: 7, target: { path: "/activity", available: true } },
      },
    });
    expect(result.analysisId).toBe(42);
    expect(result.status).toBe("fresh");
    expect(result.insights[0].evidenceIds).toEqual(["activity.week"]);
    expect(result.recommendations[0].title).toBe("Следующий шаг");
    expect(result.evidence["activity.week"]).toMatchObject({ label: "Активность", rangeStart: "2026-08-12", count: 7, target: { path: "/activity", available: true } });
  });

  it("keeps steps fail-closed to Xiaomi Cloud in data quality", () => {
    const result = normalizeDataQuality({
      range: "30d",
      from: "2026-08-01",
      to: "2026-08-02",
      timezone: "Europe/Moscow",
      sources: { mi_fitness: { status: "healthy", last_success_at: "2026-08-03T06:00:00Z" } },
      metrics: [{
        key: "steps",
        family: "activity",
        source_policy: "xiaomi_finalized_only",
        days: [
          { date: "2026-08-01", state: "available", source: "health_connect" },
          { date: "2026-08-02", state: "confirmed_empty", source: "mi_fitness" },
        ],
      }],
    }, "30d");
    expect(result.sources[0]).toMatchObject({ key: "mi_fitness", status: "healthy" });
    expect(result.metrics[0].days).toEqual([
      { date: "2026-08-01", state: "missing", source: null },
      { date: "2026-08-02", state: "confirmed_empty", source: "mi_fitness" },
    ]);
    expect(result.metrics[0].coverage).toMatchObject({ withValues: 0, confirmedEmpty: 1, missing: 1, healthConnect: 0 });
  });

  it("normalizes tasks and immutable assistant evidence", () => {
    const tasks = normalizeHealthTaskList({ items: [{
      id: "task-1", title: "Повторить измерение", note: null, next_due_at: "2026-09-04T06:00:00Z",
      recurrence: "weekly", telegram_enabled: true, status: "active", overdue: false,
      source_analysis_id: 42, source_item_id: "recommendation-1",
      source: { kind: "ai_recommendation", title: "Контроль", text: "Повторите измерение.", evidence_ids: ["pressure.latest"], generated_at: "2026-09-01T06:00:00Z" },
      created_at: "2026-09-01T06:00:00Z", updated_at: "2026-09-01T06:00:00Z",
    }], open_count: 1 });
    expect(tasks.items[0]).toMatchObject({ recurrence: "weekly", telegramEnabled: true, sourceAnalysisId: 42 });

    const assistant = normalizeAssistantMessages({ analysis_id: 42, items: [{
      id: "message-1", role: "assistant", status: "complete", content: "Значение сохранено.", draft_segments: [], evidence_keys: ["lab.ferritin"],
      evidence: { "lab.ferritin": { kind: "laboratory_result", metric: "laboratory", label: "Ферритин", value_numeric: 42, unit: "нг/мл", observed_on: "2026-08-28", verification: "verified", target: { path: null, available: false } } },
      error_code: null, created_at: "2026-09-01T06:00:00Z", updated_at: "2026-09-01T06:00:01Z",
    }], recommendations: [{ title: "Контроль", text: "Сверьте показатель.", evidence_ids: ["lab.ferritin"] }], evidence: {
      "lab.ferritin": { key: "lab.ferritin", kind: "laboratory", metric: "laboratory", label: "Ферритин", value: 42, unit: "нг/мл", date: "2026-08-28", target: { path: null, available: false } },
    } });
    expect(assistant.items[0].evidence["lab.ferritin"]).toMatchObject({ label: "Ферритин", value: 42, verification: "verified" });
    expect(assistant.analysisId).toBe(42);
    expect(assistant.recommendations[0]).toMatchObject({ id: "recommendation-1", evidenceIds: ["lab.ferritin"] });
    expect(assistant.evidence["lab.ferritin"]).toMatchObject({ label: "Ферритин", value: 42 });
  });

  it("normalizes lab comparison arrays and deterministic incompatibility", () => {
    const result = normalizeLabCompare({
      panels: [{ document_id: "doc-a", observed_on: "2026-08-01", verified: true, result_count: 1 }, { document_id: "doc-b", observed_on: "2026-09-01", verified: false, result_count: 2 }],
      rows: [{
        analyte_id: "ferritin", analyte_name: "Ферритин", comparable: false, incompatibility: "multiple_results", missing: false, status_changed: false, value_changed: false, deltas: [],
        cells: [
          [{ id: "result-a", document_id: "doc-a", analyte_id: "ferritin", analyte_name: "Ферритин", value_numeric: 42, unit: "нг/мл", status: "within_reference", verification_status: "verified" }],
          [{ id: "result-b", document_id: "doc-b", analyte_id: "ferritin", analyte_name: "Ферритин", value_numeric: 39, unit: "нг/мл", status: "within_reference", verification_status: "unverified" }, { id: "result-c", document_id: "doc-b", analyte_id: "ferritin", analyte_name: "Ферритин", value_numeric: 41, unit: "нг/мл", status: "within_reference", verification_status: "unverified" }],
        ],
      }],
    });
    expect(result.panels.map((panel) => panel.documentId)).toEqual(["doc-a", "doc-b"]);
    expect(result.rows[0]).toMatchObject({ analyteId: "ferritin", incompatibility: "multiple_results", comparable: false });
    expect(result.rows[0].cells[1]).toHaveLength(2);
  });

  it("normalizes one immutable doctor-report preview while retaining sleep minutes", () => {
    const result = normalizeDoctorReport({
      id: "report-1",
      options: { period: "90d", sections: ["activity", "recovery", "labs"] },
      preview: {
        meta: { created_at: "2026-09-02T08:00:00Z", period: "90d", from: "2026-06-06", to: "2026-09-02", timezone: "Europe/Moscow" },
        sections: {
          activity: { daily: [{ date: "2026-09-01", steps: null }], summary: { steps: null } },
          recovery: { daily: [{ date: "2026-09-01", sleep_minutes: 461 }] },
          labs: [{ analyte: "Ферритин", value: "42 нг/мл", observed_on: "2026-08-28", reference: "15–150 нг/мл", status: "within_reference", verification_status: "verified" }],
        },
      },
      page_count: 3, size_bytes: 120000, created_at: "2026-09-02T08:00:00Z", expires_at: "2026-09-03T08:00:00Z",
      download_url: "/unexpected/report.pdf",
    });
    expect(result.preview.recovery?.points[0].sleepMinutes).toBe(461);
    expect(result.preview.activity?.points[0].steps).toBeNull();
    expect(result.preview.labs?.[0].verificationStatus).toBe("verified");
    expect(result.downloadUrl).toMatch(/\/api\/v1\/reports\/doctor\/report-1\.pdf$/);
  });
});
