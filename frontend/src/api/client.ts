import type {
  ActivityPoint,
  ActivitySeriesResponse,
  AiAnalysis,
  AiAnalysisStatus,
  AiNarrativeItem,
  CompositionPoint,
  HealthCorrelation,
  Insight,
  InsightTone,
  Overview,
  Period,
  PressurePoint,
  PressureSeriesResponse,
  PressureStats,
  RecoveryPoint,
  RecoverySeriesResponse,
  SeriesMeta,
  SeriesResponse,
  SyncStatus,
  WeightPlanPoint,
  WeightProjectionPoint,
  WeightPoint,
  WeightSeriesResponse,
  WeeklyActivityPoint,
  WeeklyWeightPoint,
  AssistantMessage,
  AuthSession,
  LabDocument,
  LabResult,
  LabResultInput,
  UserProfile,
} from "./types";

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
export const API_ROOT = `${basePath}/api/v1`;

type JsonRecord = Record<string, unknown>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown): JsonRecord {
  return isRecord(value) ? value : {};
}

function unbox(value: unknown): unknown {
  const body = record(value);
  return isRecord(body.data) ? body.data : value;
}

function at(source: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => record(current)[key], source);
}

function pick(source: unknown, ...paths: string[]): unknown {
  for (const path of paths) {
    const value = at(source, path);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function number(source: unknown, ...paths: string[]): number | null {
  const value = pick(source, ...paths);
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(",", "."));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function string(source: unknown, ...paths: string[]): string | null {
  const value = pick(source, ...paths);
  return typeof value === "string" && value.trim() ? value : null;
}

function boolean(source: unknown, ...paths: string[]): boolean {
  const value = pick(source, ...paths);
  return value === true || value === 1 || value === "1" || value === "true";
}

function list(source: unknown, ...paths: string[]): unknown[] {
  for (const path of paths) {
    const value = at(source, path);
    if (Array.isArray(value)) return value;
  }
  return [];
}

function normalizedStatus(value: string | null): SyncStatus {
  if (!value) return "unknown";
  const status = value.toLowerCase();
  if (["ok", "healthy", "success", "idle"].includes(status)) return "ok";
  if (["syncing", "running", "pending"].includes(status)) return "syncing";
  if (["delayed", "stale", "warning"].includes(status)) return "delayed";
  if (["error", "failed", "unhealthy"].includes(status)) return "error";
  return "unknown";
}

function overviewSyncStatus(source: unknown): SyncStatus {
  if (boolean(source, "sync.has_error")) return "error";
  const reported = normalizedStatus(string(source, "sync.status", "sync.state"));
  if (reported !== "unknown") return reported;
  return boolean(source, "sync.initial_import_done") ? "ok" : "unknown";
}

function normalizedTone(value: string | null): InsightTone {
  if (!value) return "neutral";
  const tone = value.toLowerCase();
  if (["positive", "success", "good"].includes(tone)) return "positive";
  if (["attention", "warning", "plateau"].includes(tone)) return "attention";
  if (["achievement", "milestone"].includes(tone)) return "achievement";
  return "neutral";
}

function normalizeInsight(value: unknown, index: number): Insight | null {
  const title = string(value, "title", "name") ?? "Наблюдение";
  const text = string(value, "text", "message", "description");
  if (!text) return null;
  return {
    id: string(value, "id", "code") ?? `insight-${index}`,
    title,
    text,
    tone: normalizedTone(string(value, "tone", "severity", "kind")),
    createdAt: string(value, "created_at", "createdAt", "date"),
  };
}

export function normalizeOverview(payload: unknown): Overview {
  const body = unbox(payload);
  return {
    generatedAt: string(body, "generated_at", "generatedAt", "as_of"),
    plan: {
      startDate: string(body, "plan.start_date", "plan.startDate") ?? "",
      startWeightKg: number(body, "plan.start_weight_kg", "plan.startWeightKg") ?? 0,
      targetWeightKg: number(body, "plan.target_weight_kg", "plan.targetWeightKg") ?? 0,
      targetDate: string(body, "plan.target_date", "plan.targetDate", "plan.planned_target_date"),
      plannedTodayKg: number(
        body,
        "plan.planned_today_kg",
        "plan.plannedTodayKg",
        "weight.planned_today_kg",
        "planned_weight_today_kg",
      ),
    },
    weight: {
      latestKg: number(body, "weight.latest_kg", "weight.latestKg", "latest_weight_kg", "latest_weight.value"),
      latestAt: string(body, "weight.latest_at", "weight.latestAt", "latest_weight_at", "latest_weight.measured_at"),
      smoothed7dKg: number(body, "weight.smoothed_7d_kg", "weight.smoothed7dKg", "weight.trend_7d_kg", "rolling_7d_kg"),
      changeSinceStartKg: number(
        body,
        "weight.change_since_start_kg",
        "weight.changeSinceStartKg",
        "weight.total_change_kg",
        "change_from_start_kg",
      ),
      deviationFromPlanKg: number(
        body,
        "weight.deviation_from_plan_kg",
        "weight.deviationFromPlanKg",
        "weight.plan_delta_kg",
        "plan_delta_kg",
      ),
      progressPct: number(body, "weight.progress_pct", "weight.progressPct", "weight.goal_progress_pct", "goal_progress_percent"),
      trend28dKg: number(body, "weight.trend_28d_kg", "weight.trend28dKg", "change_28d_kg"),
      trend42dKg: number(body, "weight.trend_42d_kg", "weight.trend42dKg", "change_42d_kg"),
      forecastDate: string(body, "weight.forecast_date", "weight.forecastDate", "forecast.target_date"),
      measurementDays30d: number(
        body,
        "weight.measurement_days_30d",
        "weight.measurementDays30d",
        "weight.regularity_30d",
        "measurement_days_last_14",
      ),
    },
    pressure: {
      latestSystolic: number(body, "pressure.latest_systolic", "pressure.latestSystolic", "pressure.systolic"),
      latestDiastolic: number(body, "pressure.latest_diastolic", "pressure.latestDiastolic", "pressure.diastolic"),
      latestPulse: number(body, "pressure.latest_pulse", "pressure.latestPulse", "pressure.pulse"),
      latestAt: string(body, "pressure.latest_at", "pressure.latestAt", "pressure.measured_at"),
      avg7dSystolic: number(body, "pressure.avg_7d_systolic", "pressure.avg7dSystolic"),
      avg7dDiastolic: number(body, "pressure.avg_7d_diastolic", "pressure.avg7dDiastolic"),
      avg30dSystolic: number(body, "pressure.avg_30d_systolic", "pressure.avg30dSystolic"),
      avg30dDiastolic: number(body, "pressure.avg_30d_diastolic", "pressure.avg30dDiastolic"),
    },
    composition: {
      fatPct: number(body, "composition.fat_pct", "composition.fatPct", "composition.fat_ratio"),
      fatMassKg: number(body, "composition.fat_mass_kg", "composition.fatMassKg"),
      leanMassKg: number(body, "composition.lean_mass_kg", "composition.leanMassKg", "composition.fat_free_mass_kg"),
      measuredAt: string(body, "composition.measured_at", "composition.measuredAt", "composition.latest_at"),
    },
    sync: {
      status: overviewSyncStatus(body),
      lastSuccessAt: string(body, "sync.last_success_at", "sync.lastSuccessAt", "sync.last_sync_at"),
      nextSyncAt: string(body, "sync.next_sync_at", "sync.nextSyncAt"),
      source: string(body, "sync.source") ?? "Withings Cloud",
    },
    insights: list(body, "insights", "insights.items")
      .map(normalizeInsight)
      .filter((item): item is Insight => item !== null),
  };
}

function normalizeMeta(payload: unknown, fallbackRange: Period, pointsLength: number): SeriesMeta {
  const body = record(unbox(payload));
  const meta = record(body.meta);
  const range = (string(meta, "range") ?? string(body, "range")) as Period | null;
  return {
    range: range && ["program", "30d", "90d", "1y", "all"].includes(range) ? range : fallbackRange,
    from: string(meta, "from", "date_from") ?? string(body, "from", "date_from"),
    to: string(meta, "to", "date_to") ?? string(body, "to", "date_to"),
    count: number(meta, "count", "total") ?? number(body, "count", "total") ?? pointsLength,
    timezone: string(meta, "timezone", "tz") ?? string(body, "timezone", "tz") ?? "Europe/Moscow",
  };
}

function metaWithBounds<T extends { measuredAt: string }>(payload: unknown, range: Period, points: T[]): SeriesMeta {
  const meta = normalizeMeta(payload, range, points.length);
  return {
    ...meta,
    from: meta.from ?? points.at(0)?.measuredAt ?? null,
    to: meta.to ?? points.at(-1)?.measuredAt ?? null,
  };
}

function seriesItems(payload: unknown): unknown[] {
  const body = unbox(payload);
  return Array.isArray(body) ? body : list(body, "points", "items", "series");
}

export function normalizeWeightSeries(payload: unknown, range: Period): WeightSeriesResponse {
  const body = unbox(payload);
  const canonical = seriesItems(body);
  const source = canonical.length ? canonical : list(body, "daily").length ? list(body, "daily") : list(body, "raw");
  const points = source
    .map((value): WeightPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "timestamp", "date");
      const weightKg = number(value, "weight_kg", "weightKg", "weight", "value");
      if (!measuredAt || weightKg === null) return null;
      return {
        measuredAt,
        weightKg,
        smoothed7dKg: number(value, "smoothed_7d_kg", "smoothed7dKg", "trend_kg", "trend", "rolling_7d"),
        plannedKg: number(value, "planned_kg", "plannedKg", "plan_kg", "plan", "planned"),
        forecastKg: number(value, "forecast_kg", "forecastKg", "forecast"),
        forecastLowKg: number(value, "forecast_low_kg", "forecastLowKg", "forecast_low"),
        forecastHighKg: number(value, "forecast_high_kg", "forecastHighKg", "forecast_high"),
        isOutlier: boolean(value, "is_outlier", "isOutlier", "outlier"),
      };
    })
    .filter((point): point is WeightPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  const projection = list(body, "projection", "forecast.projection")
    .map((value): WeightProjectionPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "date");
      const forecastKg = number(value, "forecast_kg", "forecastKg", "value");
      if (!measuredAt || forecastKg === null) return null;
      return {
        measuredAt,
        forecastKg,
        forecastLowKg: number(value, "forecast_low_kg", "forecastLowKg", "low"),
        forecastHighKg: number(value, "forecast_high_kg", "forecastHighKg", "high"),
      };
    })
    .filter((point): point is WeightProjectionPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  const planProjection = list(body, "plan_projection", "plan.projection")
    .map((value): WeightPlanPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "date");
      const plannedKg = number(value, "planned_kg", "plannedKg", "value");
      return measuredAt && plannedKg !== null ? { measuredAt, plannedKg } : null;
    })
    .filter((point): point is WeightPlanPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  const weekly = list(body, "weekly", "weeks")
    .map((value): WeeklyWeightPoint | null => {
      const startDate = string(value, "start_date", "startDate");
      const endDate = string(value, "end_date", "endDate");
      if (!startDate || !endDate) return null;
      return {
        startDate,
        endDate,
        actualAvgKg: number(value, "actual_avg_kg", "actualAvgKg"),
        actualMinKg: number(value, "actual_min_kg", "actualMinKg"),
        plannedAvgKg: number(value, "planned_avg_kg", "plannedAvgKg"),
        actualChangeKg: number(value, "actual_change_kg", "actualChangeKg"),
        plannedChangeKg: number(value, "planned_change_kg", "plannedChangeKg"),
        deviationFromPlanKg: number(value, "deviation_from_plan_kg", "deviationFromPlanKg"),
        measurementDays: number(value, "measurement_days", "measurementDays") ?? 0,
        sampleCount: number(value, "sample_count", "sampleCount") ?? 0,
        outlierDays: number(value, "outlier_days", "outlierDays") ?? 0,
        isPartial: boolean(value, "is_partial", "isPartial"),
      };
    })
    .filter((point): point is WeeklyWeightPoint => point !== null)
    .sort((left, right) => left.startDate.localeCompare(right.startDate));
  return { points, projection, planProjection, weekly, meta: metaWithBounds(payload, range, points) };
}

function emptyStats(): PressureStats {
  return {
    avgSystolic: null,
    avgDiastolic: null,
    avgPulse: null,
    minSystolic: null,
    maxSystolic: null,
    minDiastolic: null,
    maxDiastolic: null,
    variabilitySystolic: null,
    variabilityDiastolic: null,
    sessions: 0,
  };
}

function normalizePressureStats(payload: unknown, key: "stats_7d" | "stats_30d"): PressureStats {
  const body = record(unbox(payload));
  const camelKey = key === "stats_7d" ? "stats7d" : "stats30d";
  const legacyKey = key === "stats_7d" ? "last_7_days" : "last_30_days";
  const stats = record(body[key] ?? body[camelKey] ?? record(body.statistics)[legacyKey]);
  if (!Object.keys(stats).length) return emptyStats();
  return {
    avgSystolic: number(stats, "avg_systolic", "avgSystolic", "mean_systolic", "systolic.mean"),
    avgDiastolic: number(stats, "avg_diastolic", "avgDiastolic", "mean_diastolic", "diastolic.mean"),
    avgPulse: number(stats, "avg_pulse", "avgPulse", "mean_pulse", "pulse.mean"),
    minSystolic: number(stats, "min_systolic", "minSystolic", "systolic.min"),
    maxSystolic: number(stats, "max_systolic", "maxSystolic", "systolic.max"),
    minDiastolic: number(stats, "min_diastolic", "minDiastolic", "diastolic.min"),
    maxDiastolic: number(stats, "max_diastolic", "maxDiastolic", "diastolic.max"),
    variabilitySystolic: number(stats, "variability_systolic", "variabilitySystolic", "stddev_systolic", "systolic.variability"),
    variabilityDiastolic: number(stats, "variability_diastolic", "variabilityDiastolic", "stddev_diastolic", "diastolic.variability"),
    sessions: number(stats, "sessions", "count") ?? 0,
  };
}

export function normalizePressureSeries(payload: unknown, range: Period): PressureSeriesResponse {
  const body = unbox(payload);
  const canonical = seriesItems(body);
  const source = canonical.length ? canonical : list(body, "sessions");
  const points = source
    .map((value): PressurePoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "timestamp", "date");
      const systolic = number(value, "systolic", "systolic_mmhg", "sys");
      const diastolic = number(value, "diastolic", "diastolic_mmhg", "dia");
      if (!measuredAt || systolic === null || diastolic === null) return null;
      const period = string(value, "period_of_day", "periodOfDay");
      return {
        measuredAt,
        systolic,
        diastolic,
        pulse: number(value, "pulse", "heart_rate", "heartRate"),
        pulsePressure: number(value, "pulse_pressure", "pulsePressure") ?? systolic - diastolic,
        sessionSize: number(value, "session_size", "sessionSize", "samples", "sample_count") ?? 1,
        periodOfDay: period === "morning" || period === "evening"
          ? period
          : (() => {
              const hour = new Date(measuredAt).toLocaleString("en-US", { hour: "2-digit", hour12: false, timeZone: "Europe/Moscow" });
              const numericHour = Number(hour);
              return numericHour >= 5 && numericHour < 12 ? "morning" : numericHour >= 18 ? "evening" : "other";
            })(),
      };
    })
    .filter((point): point is PressurePoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  return {
    points,
    meta: metaWithBounds(payload, range, points),
    stats7d: normalizePressureStats(payload, "stats_7d"),
    stats30d: normalizePressureStats(payload, "stats_30d"),
  };
}

export function normalizeCompositionSeries(payload: unknown, range: Period): SeriesResponse<CompositionPoint> {
  const body = unbox(payload);
  const canonical = seriesItems(body);
  const source = canonical.length ? canonical : (() => {
    const combined = new Map<string, JsonRecord>();
    const merge = (path: string, field: string) => list(body, path).forEach((value) => {
      const measuredAt = string(value, "measured_at", "measuredAt", "timestamp", "date");
      if (!measuredAt) return;
      const current = combined.get(measuredAt) ?? { measured_at: measuredAt };
      current[field] = number(value, "value");
      combined.set(measuredAt, current);
    });
    merge("series.fat_percent", "fat_pct");
    merge("series.fat_mass", "fat_mass_kg");
    merge("series.fat_free_mass", "lean_mass_kg");
    return [...combined.values()];
  })();
  const points = source
    .map((value): CompositionPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "timestamp", "date");
      if (!measuredAt) return null;
      const point = {
        measuredAt,
        fatPct: number(value, "fat_pct", "fatPct", "fat_ratio"),
        fatMassKg: number(value, "fat_mass_kg", "fatMassKg"),
        leanMassKg: number(value, "lean_mass_kg", "leanMassKg", "fat_free_mass_kg"),
      };
      return point.fatPct === null && point.fatMassKg === null && point.leanMassKg === null ? null : point;
    })
    .filter((point): point is CompositionPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  return { points, meta: metaWithBounds(payload, range, points) };
}

export function normalizeActivitySeries(payload: unknown, range: Period): ActivitySeriesResponse {
  const body = unbox(payload);
  const source = list(body, "points", "daily", "items");
  const points = source
    .map((value): ActivityPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "date");
      if (!measuredAt) return null;
      return {
        measuredAt,
        steps: number(value, "steps"),
        distanceKm: number(value, "distance_km", "distanceKm"),
        activeCaloriesKcal: number(value, "active_calories_kcal", "activeCaloriesKcal"),
        totalCaloriesKcal: number(value, "total_calories_kcal", "totalCaloriesKcal"),
        activeMinutes: number(value, "active_minutes", "activeMinutes"),
        workoutMinutes: number(value, "workout_minutes", "workoutMinutes"),
        workouts: number(value, "workouts", "workout_count", "workoutCount") ?? 0,
      };
    })
    .filter((point): point is ActivityPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  const weekly = list(body, "weekly", "weeks")
    .map((value): WeeklyActivityPoint | null => {
      const startDate = string(value, "start_date", "startDate");
      const endDate = string(value, "end_date", "endDate");
      if (!startDate || !endDate) return null;
      return {
        startDate,
        endDate,
        actualSteps: number(value, "actual_steps", "actualSteps", "steps"),
        baselineSteps: number(value, "baseline_steps", "baselineSteps", "expected_steps"),
        actualActiveMinutes: number(value, "actual_active_minutes", "actualActiveMinutes", "active_minutes"),
        baselineActiveMinutes: number(value, "baseline_active_minutes", "baselineActiveMinutes"),
        actualWorkoutMinutes: number(value, "actual_workout_minutes", "actualWorkoutMinutes", "workout_minutes"),
        workouts: number(value, "workouts", "workout_count", "workoutCount") ?? 0,
        coverageDays: number(value, "coverage_days.steps", "coverageDays.steps", "coverage_days", "coverageDays") ?? 0,
        isPartial: boolean(value, "is_partial", "isPartial"),
      };
    })
    .filter((point): point is WeeklyActivityPoint => point !== null)
    .sort((left, right) => left.startDate.localeCompare(right.startDate));
  const summary = record(record(body).summary);
  const latest = points.at(-1);
  const correlations = normalizeHealthCorrelations(body);
  return {
    points,
    weekly,
    summary: {
      latestDate: string(summary, "latest_date", "latestDate") ?? latest?.measuredAt ?? null,
      steps: number(summary, "steps", "latest_steps") ?? latest?.steps ?? null,
      baselineSteps: number(summary, "baseline_steps", "baselineSteps"),
      distanceKm: number(summary, "distance_km", "distanceKm") ?? latest?.distanceKm ?? null,
      activeCaloriesKcal: number(summary, "active_calories_kcal", "activeCaloriesKcal") ?? latest?.activeCaloriesKcal ?? null,
      activeMinutes: number(summary, "active_minutes", "activeMinutes") ?? latest?.activeMinutes ?? null,
      workouts7d: number(summary, "workouts_7d", "workouts7d") ?? points.slice(-7).reduce((total, point) => total + point.workouts, 0),
      dataAsOf: string(summary, "data_as_of", "dataAsOf") ?? string(body, "data_as_of", "dataAsOf"),
    },
    correlations,
    meta: metaWithBounds(payload, range, points),
  };
}

function normalizeHealthCorrelations(body: unknown): HealthCorrelation[] {
  const defaultDisclaimer = string(body, "correlation_policy.disclaimer", "correlationPolicy.disclaimer")
    ?? "Корреляция не доказывает причинность.";
  return list(body, "correlations")
    .map((value): HealthCorrelation | null => {
      const metric = string(value, "metric", "source_metric", "sourceMetric");
      const target = string(value, "target", "target_metric", "targetMetric");
      const coefficient = number(value, "coefficient");
      const fullOverlappingWeeks = number(value, "full_overlapping_weeks", "fullOverlappingWeeks");
      if (!metric || !target || coefficient === null || fullOverlappingWeeks === null) return null;
      return {
        metric,
        target,
        coefficient,
        fullOverlappingWeeks,
        disclaimer: string(value, "disclaimer") ?? defaultDisclaimer,
      };
    })
    .filter((value): value is HealthCorrelation => value !== null);
}

export function normalizeRecoverySeries(payload: unknown, range: Period): RecoverySeriesResponse {
  const body = unbox(payload);
  const points = list(body, "points", "daily", "items")
    .map((value): RecoveryPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt", "date");
      if (!measuredAt) return null;
      return {
        measuredAt,
        sleepMinutes: number(value, "sleep_minutes", "sleepMinutes"),
        deepSleepMinutes: number(value, "deep_sleep_minutes", "deepSleepMinutes"),
        remSleepMinutes: number(value, "rem_sleep_minutes", "remSleepMinutes"),
        awakeMinutes: number(value, "awake_minutes", "awakeMinutes"),
        restingHeartRateBpm: number(value, "resting_heart_rate_bpm", "restingHeartRateBpm"),
        averageHeartRateBpm: number(value, "average_heart_rate_bpm", "averageHeartRateBpm"),
        minimumHeartRateBpm: number(value, "minimum_heart_rate_bpm", "minimumHeartRateBpm"),
        maximumHeartRateBpm: number(value, "maximum_heart_rate_bpm", "maximumHeartRateBpm"),
        hrvRmssdMs: number(value, "hrv_rmssd_ms", "hrvRmssdMs", "hrv_ms"),
        spo2Pct: number(value, "oxygen_saturation_pct", "spo2_pct", "spo2Pct"),
        vo2Max: number(value, "vo2_max_ml_kg_min", "vo2_max", "vo2Max"),
      };
    })
    .filter((point): point is RecoveryPoint => point !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  const summary = record(record(body).summary);
  const latest = points.at(-1);
  return {
    points,
    summary: {
      latestDate: string(summary, "latest_date", "latestDate") ?? latest?.measuredAt ?? null,
      sleepMinutes: number(summary, "sleep_minutes", "sleepMinutes") ?? latest?.sleepMinutes ?? null,
      baselineSleepMinutes: number(summary, "baseline_sleep_minutes", "baselineSleepMinutes"),
      averageHeartRateBpm: number(summary, "average_heart_rate_bpm", "averageHeartRateBpm") ?? latest?.averageHeartRateBpm ?? null,
      minimumHeartRateBpm: number(summary, "minimum_heart_rate_bpm", "minimumHeartRateBpm") ?? latest?.minimumHeartRateBpm ?? null,
      maximumHeartRateBpm: number(summary, "maximum_heart_rate_bpm", "maximumHeartRateBpm") ?? latest?.maximumHeartRateBpm ?? null,
      restingHeartRateBpm: number(summary, "resting_heart_rate_bpm", "restingHeartRateBpm") ?? latest?.restingHeartRateBpm ?? null,
      baselineRestingHeartRateBpm: number(summary, "baseline_resting_heart_rate_bpm", "baselineRestingHeartRateBpm"),
      hrvRmssdMs: number(summary, "hrv_rmssd_ms", "hrvRmssdMs") ?? latest?.hrvRmssdMs ?? null,
      baselineHrvRmssdMs: number(summary, "baseline_hrv_rmssd_ms", "baselineHrvRmssdMs"),
      spo2Pct: number(summary, "oxygen_saturation_pct", "spo2_pct", "spo2Pct") ?? latest?.spo2Pct ?? null,
      dataAsOf: string(summary, "data_as_of", "dataAsOf") ?? string(body, "data_as_of", "dataAsOf"),
    },
    availableMetrics: list(body, "available_metrics", "availableMetrics").filter((value): value is string => typeof value === "string"),
    correlations: normalizeHealthCorrelations(body),
    meta: metaWithBounds(payload, range, points),
  };
}

function normalizeAiItem(value: unknown, index: number, prefix: string): AiNarrativeItem | null {
  const textValue = string(value, "text", "message", "description");
  if (!textValue) return null;
  return {
    id: string(value, "id") ?? `${prefix}-${index}`,
    title: string(value, "title") ?? (prefix === "recommendation" ? "Рекомендация" : "Наблюдение"),
    text: textValue,
    evidenceIds: list(value, "evidence_ids", "evidenceIds").filter((item): item is string => typeof item === "string"),
  };
}

export function normalizeAiAnalysis(payload: unknown): AiAnalysis {
  const body = unbox(payload);
  const statusValue = string(body, "status") as AiAnalysisStatus | null;
  const status: AiAnalysisStatus = statusValue && ["fresh", "stale", "unavailable", "pending"].includes(statusValue)
    ? statusValue
    : string(body, "generated_at", "generatedAt") ? "fresh" : "unavailable";
  return {
    status,
    headline: string(body, "headline"),
    summary: string(body, "summary"),
    insights: list(body, "insights").map((value, index) => normalizeAiItem(value, index, "insight")).filter((value): value is AiNarrativeItem => value !== null),
    recommendations: list(body, "recommendations").map((value, index) => normalizeAiItem(value, index, "recommendation")).filter((value): value is AiNarrativeItem => value !== null),
    limitations: list(body, "limitations").filter((value): value is string => typeof value === "string"),
    generatedAt: string(body, "generated_at", "generatedAt"),
    dataAsOf: string(body, "data_as_of", "dataAsOf"),
    model: string(body, "model"),
  };
}

function csrfToken(): string | null {
  const prefix = "__Secure-amigo_csrf=";
  const value = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : null;
}

async function requestJson(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<unknown> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    signal,
    credentials: "same-origin",
    headers,
  });
  if (response.status === 401) window.dispatchEvent(new Event("amigo:unauthorized"));
  if (!response.ok) {
    let detail = `Сервер вернул ${response.status}`;
    try {
      const body = record(await response.json());
      detail = string(body, "detail", "message") ?? detail;
    } catch {
      // The status code is enough when the body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return {};
  return response.json();
}

async function fetchJson(path: string, signal?: AbortSignal): Promise<unknown> {
  return requestJson(path, {}, signal);
}

function jsonBody(value: unknown): Pick<RequestInit, "method" | "headers" | "body"> {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
}

function queryRange(range: Period): string {
  return `?${new URLSearchParams({ range }).toString()}`;
}

export const api = {
  session: async (signal?: AbortSignal) => requestJson("/auth/session", {}, signal) as Promise<AuthSession>,
  login: async (username: string, password: string) => requestJson("/auth/login", jsonBody({ username, password })) as Promise<AuthSession>,
  logout: async () => requestJson("/auth/logout", { method: "POST" }),
  profile: async (signal?: AbortSignal) => fetchJson("/profile", signal) as Promise<UserProfile>,
  updateProfile: async (profile: Partial<{ birth_date: string | null; reference_sex: string | null; accept_ai_data_processing: boolean }>) =>
    requestJson("/profile", { ...jsonBody(profile), method: "PATCH" }) as Promise<UserProfile>,
  overview: async (signal?: AbortSignal) => normalizeOverview(await fetchJson("/overview", signal)),
  weight: async (range: Period, signal?: AbortSignal) =>
    normalizeWeightSeries(await fetchJson(`/series/weight${queryRange(range)}`, signal), range),
  pressure: async (range: Period, signal?: AbortSignal) =>
    normalizePressureSeries(await fetchJson(`/series/pressure${queryRange(range)}`, signal), range),
  composition: async (range: Period, signal?: AbortSignal) =>
    normalizeCompositionSeries(await fetchJson(`/series/composition${queryRange(range)}`, signal), range),
  activity: async (range: Period, signal?: AbortSignal) =>
    normalizeActivitySeries(await fetchJson(`/series/activity${queryRange(range)}`, signal), range),
  recovery: async (range: Period, signal?: AbortSignal) =>
    normalizeRecoverySeries(await fetchJson(`/series/recovery${queryRange(range)}`, signal), range),
  aiAnalysis: async (signal?: AbortSignal) => normalizeAiAnalysis(await fetchJson("/ai-analysis", signal)),
  insights: async (signal?: AbortSignal): Promise<Insight[]> => {
    const payload = unbox(await fetchJson("/insights", signal));
    return list(payload, "items", "insights")
      .map(normalizeInsight)
      .filter((item): item is Insight => item !== null);
  },
  labDocuments: async (signal?: AbortSignal) => {
    const body = await fetchJson("/labs/documents", signal) as { items: LabDocument[] };
    return body.items;
  },
  labDocument: async (id: string, signal?: AbortSignal) => fetchJson(`/labs/documents/${id}`, signal) as Promise<LabDocument>,
  uploadLab: async (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return requestJson("/labs/documents", { method: "POST", body: form }) as Promise<LabDocument>;
  },
  confirmLab: async (id: string) => requestJson(`/labs/documents/${id}/confirm`, { method: "POST" }) as Promise<LabDocument>,
  retryLab: async (id: string) => requestJson(`/labs/documents/${id}/retry`, { method: "POST" }) as Promise<LabDocument>,
  deleteLab: async (id: string) => requestJson(`/labs/documents/${id}`, { method: "DELETE" }),
  patchLabResult: async (id: string, patch: Record<string, unknown>) =>
    requestJson(`/labs/results/${id}`, { ...jsonBody(patch), method: "PATCH" }) as Promise<LabResult>,
  createLabResult: async (documentId: string, result: LabResultInput) =>
    requestJson(`/labs/documents/${documentId}/results`, jsonBody(result)) as Promise<LabResult>,
  labSummary: async (signal?: AbortSignal) => fetchJson("/labs/summary", signal) as Promise<{ items: LabResult[]; counts: Record<string, number> }>,
  labHistory: async (analyteId: string, signal?: AbortSignal) =>
    fetchJson(`/labs/analytes/${encodeURIComponent(analyteId)}/history`, signal) as Promise<{ analyte_id: string; items: LabResult[] }>,
  assistantMessages: async (signal?: AbortSignal) =>
    fetchJson("/assistant/messages", signal) as Promise<{ items: AssistantMessage[]; recommendations: AiNarrativeItem[] }>,
  sendAssistantMessage: async (content: string, clientRequestId: string) =>
    requestJson("/assistant/messages", jsonBody({ content, client_request_id: clientRequestId })) as Promise<AssistantMessage>,
  retryAssistantMessage: async (id: string) =>
    requestJson(`/assistant/messages/${id}/retry`, { method: "POST" }) as Promise<AssistantMessage>,
  clearAssistantHistory: async () => requestJson("/assistant/history", { method: "DELETE" }),
};

export function assistantEventsUrl(messageId: string): string {
  return `${API_ROOT}/assistant/messages/${messageId}/events`;
}

export function labDownloadUrl(documentId: string): string {
  return `${API_ROOT}/labs/documents/${documentId}/download`;
}

export function csvUrl(kind: "weight" | "pressure" | "composition" | "activity" | "recovery", range: Period): string {
  return `${API_ROOT}/export/${kind}.csv?${new URLSearchParams({ range }).toString()}`;
}
