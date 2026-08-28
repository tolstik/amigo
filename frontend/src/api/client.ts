import type {
  ActivityPoint,
  ActivitySeriesResponse,
  AiAnalysis,
  AiAnalysisStatus,
  AiNarrativeItem,
  CompositionPoint,
  CircumferencePoint,
  CircumferenceSeriesResponse,
  HealthCorrelation,
  HeartRateHourlyPoint,
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
  DataQualityDay,
  DataQualityMetric,
  DataQualityRange,
  DataQualityResponse,
  DataQualitySource,
  DataSourceStatus,
  DoctorReport,
  DoctorReportAiItem,
  DoctorReportLabItem,
  DoctorReportPeriod,
  DoctorReportSection,
  DoctorReportStudyItem,
  EvidenceDescriptor,
  EvidenceMap,
  HealthTask,
  HealthTaskInput,
  HealthTaskList,
  HealthTaskPatch,
  HealthTaskSource,
  LabAnalyteGuide,
  LabCompareDelta,
  LabCompareIncompatibility,
  LabComparePanel,
  LabCompareResponse,
  LabCompareRow,
  LabDocument,
  LabResult,
  LabResultInput,
  UserProfile,
  StudyDocument,
  StudyModality,
  TaskRecurrence,
  TaskStateFilter,
  TaskStatus,
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

export function normalizeCircumferenceSeries(payload: unknown, range: Period): CircumferenceSeriesResponse {
  const body = unbox(payload);
  const points = seriesItems(body)
    .map((value): CircumferencePoint | null => {
      const measuredOn = string(value, "measured_on", "measuredOn", "date");
      if (!measuredOn) return null;
      const waistCm = number(value, "waist_cm", "waistCm", "waist");
      const hipCm = number(value, "hip_cm", "hipCm", "hips", "hip");
      if (waistCm === null && hipCm === null) return null;
      return { measuredOn, waistCm, hipCm };
    })
    .filter((point): point is CircumferencePoint => point !== null)
    .sort((left, right) => left.measuredOn.localeCompare(right.measuredOn));
  return { points, unit: "cm", meta: normalizeMeta(payload, range, points.length) };
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
  const heartRateHourly = list(body, "heart_rate_hourly", "heartRateHourly")
    .map((value): HeartRateHourlyPoint | null => {
      const measuredAt = string(value, "measured_at", "measuredAt");
      const averageBpm = number(value, "average_bpm", "averageBpm");
      const minimumBpm = number(value, "minimum_bpm", "minimumBpm");
      const maximumBpm = number(value, "maximum_bpm", "maximumBpm");
      const sampleCount = number(value, "sample_count", "sampleCount");
      return measuredAt && averageBpm !== null && minimumBpm !== null && maximumBpm !== null && sampleCount !== null
        ? { measuredAt, averageBpm, minimumBpm, maximumBpm, sampleCount }
        : null;
    })
    .filter((value): value is HeartRateHourlyPoint => value !== null)
    .sort((left, right) => left.measuredAt.localeCompare(right.measuredAt));
  return {
    points,
    heartRateHourly,
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
    id: string(value, "id") ?? `${prefix}-${index + 1}`,
    title: string(value, "title") ?? (prefix === "recommendation" ? "Рекомендация" : "Наблюдение"),
    text: textValue,
    evidenceIds: list(value, "evidence_ids", "evidenceIds", "evidence_keys").filter((item): item is string => typeof item === "string"),
  };
}

const evidenceMetricLabels: Record<string, string> = {
  profile: "Профиль",
  weight: "Вес",
  composition: "Состав тела",
  activity: "Активность",
  sleep: "Сон",
  recovery: "Восстановление",
  heart: "Пульс и сердце",
  oxygen: "Сатурация",
  vo2: "VO₂ max",
  pressure: "Давление",
  quality: "Качество данных",
  correlation: "Корреляция",
  laboratory: "Лабораторный результат",
};

function scalar(value: unknown): number | string | boolean | null {
  return typeof value === "number" || typeof value === "string" || typeof value === "boolean" ? value : null;
}

export function normalizeEvidenceMap(payload: unknown): EvidenceMap {
  const source = record(payload);
  return Object.entries(source).reduce<EvidenceMap>((result, [fallbackKey, raw]) => {
    if (!isRecord(raw)) return result;
    const key = string(raw, "key") ?? fallbackKey;
    const metric = string(raw, "metric") ?? "unknown";
    const range = record(raw.range);
    const target = record(raw.target);
    const referenceStatus = string(raw, "reference_status", "status");
    const descriptor: EvidenceDescriptor = {
      key,
      kind: string(raw, "kind") ?? "fact",
      metric,
      label: string(raw, "label") ?? evidenceMetricLabels[metric] ?? "Основание",
      value: scalar(pick(raw, "value", "value_numeric")),
      text: string(raw, "text", "value_text"),
      comparator: string(raw, "comparator"),
      unit: string(raw, "unit"),
      observedOn: string(raw, "date", "observed_on"),
      period: string(raw, "period"),
      rangeStart: string(range, "from") ?? string(raw, "range_start"),
      rangeEnd: string(range, "to") ?? string(raw, "range_end"),
      count: number(raw, "count"),
      referenceLow: number(range, "low") ?? number(raw, "reference_low"),
      referenceHigh: number(range, "high") ?? number(raw, "reference_high"),
      referenceText: string(range, "text") ?? string(raw, "reference_text"),
      referenceStatus,
      verification: string(raw, "verification"),
      target: {
        path: string(target, "path"),
        available: boolean(target, "available"),
      },
    };
    result[key] = descriptor;
    return result;
  }, {});
}

export function normalizeAiAnalysis(payload: unknown): AiAnalysis {
  const body = unbox(payload);
  const statusValue = string(body, "status") as AiAnalysisStatus | null;
  const status: AiAnalysisStatus = statusValue && ["fresh", "stale", "unavailable", "pending"].includes(statusValue)
    ? statusValue
    : string(body, "generated_at", "generatedAt") ? "fresh" : "unavailable";
  return {
    analysisId: number(body, "analysis_id", "analysisId"),
    status,
    headline: string(body, "headline"),
    summary: string(body, "summary"),
    insights: list(body, "insights").map((value, index) => normalizeAiItem(value, index, "insight")).filter((value): value is AiNarrativeItem => value !== null),
    recommendations: list(body, "recommendations").map((value, index) => normalizeAiItem(value, index, "recommendation")).filter((value): value is AiNarrativeItem => value !== null),
    limitations: list(body, "limitations").filter((value): value is string => typeof value === "string"),
    generatedAt: string(body, "generated_at", "generatedAt"),
    dataAsOf: string(body, "data_as_of", "dataAsOf"),
    model: string(body, "model"),
    evidence: normalizeEvidenceMap(at(body, "evidence")),
  };
}

function normalizedDataSourceStatus(value: string | null): DataSourceStatus {
  return value && ["healthy", "pending", "delayed", "error", "not_configured"].includes(value)
    ? value as DataSourceStatus
    : "not_configured";
}

function normalizedDataDay(value: unknown, stepsOnly: boolean): DataQualityDay | null {
  const date = string(value, "date");
  const rawState = string(value, "state");
  if (!date || !rawState || !["available", "confirmed_empty", "missing"].includes(rawState)) return null;
  const rawSource = string(value, "source");
  if (stepsOnly && rawSource !== "mi_fitness") return { date, state: "missing", source: null };
  return {
    date,
    state: rawState as DataQualityDay["state"],
    source: rawSource,
  };
}

function normalizedDataMetric(value: unknown): DataQualityMetric | null {
  const key = string(value, "key");
  if (!key) return null;
  const days = list(value, "days")
    .map((day) => normalizedDataDay(day, key === "steps"))
    .filter((day): day is DataQualityDay => day !== null)
    .sort((left, right) => left.date.localeCompare(right.date));
  const known = days.filter((day) => day.state !== "missing");
  const available = days.filter((day) => day.state === "available");
  const confirmedEmpty = days.filter((day) => day.state === "confirmed_empty");
  const missing = days.filter((day) => day.state === "missing");
  const status = available.length === days.length && days.length
    ? "available"
    : confirmedEmpty.length === days.length && days.length
      ? "confirmed_empty"
      : known.length === 0
        ? "missing"
        : "partial";
  const latest = available.at(-1) ?? null;
  return {
    key,
    family: string(value, "family") ?? "other",
    sourcePolicy: string(value, "source_policy", "sourcePolicy") ?? "unknown",
    status,
    latestDate: latest?.date ?? null,
    latestSource: latest?.source ?? null,
    observationDays: available.length,
    coverage: {
      known: known.length,
      withValues: available.length,
      withings: known.filter((day) => day.source === "withings").length,
      miFitness: known.filter((day) => day.source === "mi_fitness").length,
      healthConnect: known.filter((day) => day.source === "health_connect").length,
      confirmedEmpty: confirmedEmpty.length,
      missing: missing.length,
    },
    days,
  };
}

export function normalizeDataQuality(payload: unknown, fallbackRange: DataQualityRange): DataQualityResponse {
  const body = unbox(payload);
  const rawRange = string(body, "range");
  const range: DataQualityRange = rawRange === "90d" ? "90d" : rawRange === "30d" ? "30d" : fallbackRange;
  const sources = Object.entries(record(at(body, "sources"))).map(([key, value]): DataQualitySource => ({
    key,
    status: normalizedDataSourceStatus(string(value, "status")),
    lastSuccessAt: string(value, "last_success_at", "lastSuccessAt"),
    dataAsOf: string(value, "data_as_of", "dataAsOf"),
  }));
  return {
    range,
    from: string(body, "from") ?? "",
    to: string(body, "to") ?? "",
    timezone: string(body, "timezone") ?? "Europe/Moscow",
    generatedAt: string(body, "generated_at", "generatedAt"),
    sources,
    metrics: list(body, "metrics").map(normalizedDataMetric).filter((value): value is DataQualityMetric => value !== null),
  };
}

function normalizedTaskStatus(value: string | null): TaskStatus {
  return value && ["active", "completed", "cancelled"].includes(value) ? value as TaskStatus : "active";
}

function normalizedRecurrence(value: string | null): TaskRecurrence {
  return value && ["once", "daily", "weekly", "monthly"].includes(value) ? value as TaskRecurrence : "once";
}

function normalizeTaskSource(value: unknown): HealthTaskSource | null {
  if (!isRecord(value)) return null;
  const textValue = string(value, "text");
  if (!textValue) return null;
  return {
    kind: string(value, "kind") ?? "ai_recommendation",
    title: string(value, "title") ?? "Рекомендация",
    text: textValue,
    evidenceIds: list(value, "evidence_ids", "evidenceIds").filter((item): item is string => typeof item === "string"),
    generatedAt: string(value, "generated_at", "generatedAt"),
  };
}

export function normalizeHealthTask(payload: unknown): HealthTask {
  const body = unbox(payload);
  return {
    id: string(body, "id") ?? "",
    title: string(body, "title") ?? "Задача",
    note: string(body, "note"),
    nextDueAt: string(body, "next_due_at", "nextDueAt"),
    recurrence: normalizedRecurrence(string(body, "recurrence")),
    telegramEnabled: boolean(body, "telegram_enabled", "telegramEnabled"),
    status: normalizedTaskStatus(string(body, "status")),
    overdue: boolean(body, "overdue"),
    sourceAnalysisId: number(body, "source_analysis_id", "sourceAnalysisId"),
    sourceItemId: string(body, "source_item_id", "sourceItemId"),
    source: normalizeTaskSource(at(body, "source")),
    createdAt: string(body, "created_at", "createdAt") ?? "",
    updatedAt: string(body, "updated_at", "updatedAt") ?? "",
    completedAt: string(body, "completed_at", "completedAt"),
    cancelledAt: string(body, "cancelled_at", "cancelledAt"),
  };
}

export function normalizeHealthTaskList(payload: unknown): HealthTaskList {
  const body = unbox(payload);
  return {
    items: list(body, "items").map(normalizeHealthTask).filter((item) => Boolean(item.id)),
    openCount: number(body, "open_count", "openCount") ?? 0,
  };
}

const labStatuses = ["within_reference", "below_reference", "above_reference", "outside_reference", "indeterminate"] as const;

function normalizeLabResult(value: unknown): LabResult | null {
  const id = string(value, "id");
  if (!id) return null;
  const statusValue = string(value, "status");
  const verification = string(value, "verification_status");
  const referenceSource = string(value, "reference_source");
  return {
    id,
    document_id: string(value, "document_id") ?? "",
    analyte_id: string(value, "analyte_id"),
    analyte_name: string(value, "analyte_name") ?? "Показатель",
    value_numeric: number(value, "value_numeric"),
    value_text: string(value, "value_text"),
    comparator: string(value, "comparator"),
    unit: string(value, "unit"),
    observed_on: string(value, "observed_on"),
    specimen: string(value, "specimen"),
    method: string(value, "method"),
    reference_low: number(value, "reference_low"),
    reference_high: number(value, "reference_high"),
    reference_text: string(value, "reference_text"),
    reference_source: referenceSource && ["laboratory", "catalog", "user", "none"].includes(referenceSource)
      ? referenceSource as LabResult["reference_source"] : "none",
    laboratory_flag: string(value, "laboratory_flag"),
    status: statusValue && labStatuses.includes(statusValue as typeof labStatuses[number])
      ? statusValue as LabResult["status"] : "indeterminate",
    verification_status: verification && ["unverified", "verified", "corrected"].includes(verification)
      ? verification as LabResult["verification_status"] : "unverified",
    source_page: number(value, "source_page"),
    deleted: boolean(value, "deleted"),
  };
}

const incompatibilities = ["missing_result", "multiple_results", "non_numeric_value", "qualified_value", "different_unit", "different_specimen", "different_method"] as const;

export function normalizeLabCompare(payload: unknown): LabCompareResponse {
  const body = unbox(payload);
  const panels = list(body, "panels").map((value): LabComparePanel | null => {
    const documentId = string(value, "document_id", "documentId");
    return documentId ? {
      documentId,
      observedOn: string(value, "observed_on", "observedOn"),
      verified: boolean(value, "verified"),
      resultCount: number(value, "result_count", "resultCount") ?? 0,
    } : null;
  }).filter((value): value is LabComparePanel => value !== null);
  const rows = list(body, "rows").map((value): LabCompareRow | null => {
    const analyteName = string(value, "analyte_name", "analyteName");
    if (!analyteName) return null;
    const cells = list(value, "cells").map((cell) => Array.isArray(cell)
      ? cell.map(normalizeLabResult).filter((item): item is LabResult => item !== null)
      : []);
    const reason = string(value, "incompatibility");
    const deltas = list(value, "deltas").map((delta): LabCompareDelta | null => {
      const fromDocumentId = string(delta, "from_document_id", "fromDocumentId");
      const toDocumentId = string(delta, "to_document_id", "toDocumentId");
      const absolute = number(delta, "absolute");
      return fromDocumentId && toDocumentId && absolute !== null ? {
        fromDocumentId,
        toDocumentId,
        absolute,
        percent: number(delta, "percent"),
      } : null;
    }).filter((delta): delta is LabCompareDelta => delta !== null);
    return {
      analyteId: string(value, "analyte_id", "analyteId"),
      analyteName,
      cells,
      comparable: boolean(value, "comparable"),
      incompatibility: reason && incompatibilities.includes(reason as typeof incompatibilities[number])
        ? reason as LabCompareIncompatibility : null,
      deltas,
      missing: boolean(value, "missing"),
      statusChanged: boolean(value, "status_changed", "statusChanged"),
      valueChanged: boolean(value, "value_changed", "valueChanged"),
    };
  }).filter((value): value is LabCompareRow => value !== null);
  return { panels, rows };
}

const doctorSections = ["summary", "weight", "circumference", "pressure", "activity", "recovery", "labs", "studies", "ai"] as const;

export function normalizeDoctorReport(payload: unknown): DoctorReport {
  const body = unbox(payload);
  const id = string(body, "id") ?? "";
  const options = record(at(body, "options"));
  const rawPeriod = string(options, "period");
  const period: DoctorReportPeriod = rawPeriod === "30d" || rawPeriod === "1y" ? rawPeriod : "90d";
  const sections = list(options, "sections").filter((value): value is DoctorReportSection =>
    typeof value === "string" && doctorSections.includes(value as typeof doctorSections[number]));
  const rawPreview = record(at(body, "preview"));
  const meta = record(rawPreview.meta);
  const rawPreviewSections = record(rawPreview.sections);
  const rawCircumference = at(rawPreviewSections, "circumference");
  const circumference = isRecord(rawCircumference)
    ? normalizeCircumferenceSeries(rawCircumference, period)
    : null;
  const rawLabs = at(rawPreviewSections, "labs");
  const labs = Array.isArray(rawLabs) ? rawLabs.map((value): DoctorReportLabItem | null => {
    const analyte = string(value, "analyte");
    const resultValue = string(value, "value");
    if (!analyte || !resultValue) return null;
    const rawStatus = string(value, "status");
    return {
      analyte,
      value: resultValue,
      observedOn: string(value, "observed_on", "observedOn"),
      reference: string(value, "reference"),
      status: rawStatus && labStatuses.includes(rawStatus as typeof labStatuses[number]) ? rawStatus as LabResult["status"] : "indeterminate",
      verificationStatus: string(value, "verification_status") === "corrected" ? "corrected" : "verified",
    };
  }).filter((value): value is DoctorReportLabItem => value !== null) : null;
  const rawStudies = at(rawPreviewSections, "studies");
  const studies = Array.isArray(rawStudies) ? rawStudies.map((value): DoctorReportStudyItem | null => {
    const modality = string(value, "modality");
    if (!modality || !["ultrasound", "mri", "ct", "xray", "ecg", "other"].includes(modality)) return null;
    return {
      modality: modality as StudyModality,
      observedOn: string(value, "observed_on", "observedOn"),
      findings: list(value, "findings").filter((item): item is string => typeof item === "string"),
      conclusion: string(value, "conclusion"),
    };
  }).filter((value): value is DoctorReportStudyItem => value !== null) : null;
  const rawAi = at(rawPreviewSections, "ai");
  const ai = Array.isArray(rawAi) ? rawAi.map((value): DoctorReportAiItem | null => {
    const textValue = string(value, "text");
    return textValue ? {
      title: string(value, "title") ?? "Рекомендация",
      text: textValue,
      evidenceIds: list(value, "evidence_ids", "evidenceIds").filter((item): item is string => typeof item === "string"),
    } : null;
  }).filter((value): value is DoctorReportAiItem => value !== null) : null;
  const reportedDownload = string(body, "download_url", "downloadUrl");
  const exactDownload = `${API_ROOT}/reports/doctor/${encodeURIComponent(id)}.pdf`;
  const exactHtmlDownload = `${API_ROOT}/reports/doctor/${encodeURIComponent(id)}.html`;
  return {
    id,
    period,
    sections,
    preview: {
      meta: {
        createdAt: string(meta, "created_at", "createdAt"),
        period,
        from: string(meta, "from"),
        to: string(meta, "to"),
        timezone: string(meta, "timezone") ?? "Europe/Moscow",
      },
      summary: isRecord(rawPreviewSections.summary) ? rawPreviewSections.summary : null,
      weight: isRecord(rawPreviewSections.weight) ? normalizeWeightSeries(rawPreviewSections.weight, period) : null,
      circumference,
      pressure: isRecord(rawPreviewSections.pressure) ? normalizePressureSeries(rawPreviewSections.pressure, period) : null,
      activity: isRecord(rawPreviewSections.activity) ? normalizeActivitySeries(rawPreviewSections.activity, period) : null,
      recovery: isRecord(rawPreviewSections.recovery) ? normalizeRecoverySeries(rawPreviewSections.recovery, period) : null,
      labs,
      studies,
      ai,
    },
    pageCount: number(body, "page_count", "pageCount") ?? 0,
    sizeBytes: number(body, "size_bytes", "sizeBytes") ?? 0,
    createdAt: string(body, "created_at", "createdAt") ?? "",
    expiresAt: string(body, "expires_at", "expiresAt") ?? "",
    downloadUrl: reportedDownload === exactDownload ? reportedDownload : exactDownload,
    htmlDownloadUrl: exactHtmlDownload,
    htmlSizeBytes: number(body, "html_size_bytes", "htmlSizeBytes") ?? 0,
  };
}

function normalizeAssistantMessage(value: unknown): AssistantMessage | null {
  const id = string(value, "id");
  const role = string(value, "role");
  const status = string(value, "status");
  if (!id || (role !== "user" && role !== "assistant") || !status || !["queued", "streaming", "validating", "complete", "failed"].includes(status)) return null;
  const drafts = list(value, "draft_segments").map((segment) => ({
    text: string(segment, "text") ?? "",
    evidence_keys: list(segment, "evidence_keys").filter((item): item is string => typeof item === "string"),
  })).filter((segment) => segment.text);
  return {
    id,
    role,
    status: status as AssistantMessage["status"],
    content: string(value, "content") ?? "",
    draft_segments: drafts,
    evidence_keys: list(value, "evidence_keys").filter((item): item is string => typeof item === "string"),
    evidence: normalizeEvidenceMap(at(value, "evidence")),
    error_code: string(value, "error_code"),
    created_at: string(value, "created_at") ?? "",
    updated_at: string(value, "updated_at") ?? "",
  };
}

export function normalizeAssistantMessages(payload: unknown): {
  items: AssistantMessage[];
  analysisId: number | null;
  recommendations: AiNarrativeItem[];
  evidence: EvidenceMap;
} {
  const body = unbox(payload);
  return {
    items: list(body, "items").map(normalizeAssistantMessage).filter((item): item is AssistantMessage => item !== null),
    analysisId: number(body, "analysis_id", "analysisId"),
    recommendations: list(body, "recommendations").map((value, index) => normalizeAiItem(value, index, "recommendation")).filter((item): item is AiNarrativeItem => item !== null),
    evidence: normalizeEvidenceMap(at(body, "evidence")),
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
  const blocking = !["GET", "HEAD", "OPTIONS"].includes(method);
  if (blocking) window.dispatchEvent(new Event("amigo:loading:start"));
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      signal,
      credentials: "same-origin",
      headers,
    });
  } finally {
    if (blocking) window.dispatchEvent(new Event("amigo:loading:end"));
  }
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
  circumference: async (range: Period, signal?: AbortSignal) =>
    normalizeCircumferenceSeries(await fetchJson(`/series/circumference${queryRange(range)}`, signal), range),
  saveCircumference: async (date: string, values: { waist_cm: number | null; hip_cm: number | null }) =>
    requestJson(`/body-measurements/${encodeURIComponent(date)}`, { ...jsonBody(values), method: "PUT" }),
  deleteCircumference: async (date: string) =>
    requestJson(`/body-measurements/${encodeURIComponent(date)}`, { method: "DELETE" }),
  activity: async (range: Period, signal?: AbortSignal) =>
    normalizeActivitySeries(await fetchJson(`/series/activity${queryRange(range)}`, signal), range),
  recovery: async (range: Period, signal?: AbortSignal) =>
    normalizeRecoverySeries(await fetchJson(`/series/recovery${queryRange(range)}`, signal), range),
  dataQuality: async (range: DataQualityRange, signal?: AbortSignal) =>
    normalizeDataQuality(await fetchJson(`/data-quality?${new URLSearchParams({ range }).toString()}`, signal), range),
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
    return requestJson("/labs/uploads", { method: "POST", body: form }) as Promise<LabDocument>;
  },
  studies: async (signal?: AbortSignal) => {
    const body = await fetchJson("/studies/documents", signal) as { items: StudyDocument[] };
    return body.items;
  },
  study: async (id: string, signal?: AbortSignal) => fetchJson(`/studies/documents/${id}`, signal) as Promise<StudyDocument>,
  uploadStudy: async (file: File, modality: StudyModality, title?: string, observedOn?: string) => {
    const form = new FormData();
    form.set("file", file);
    form.set("modality", modality);
    if (title) form.set("title", title);
    if (observedOn) form.set("observed_on", observedOn);
    return requestJson("/studies/uploads", { method: "POST", body: form }) as Promise<StudyDocument>;
  },
  patchStudy: async (id: string, patch: Record<string, unknown>) =>
    requestJson(`/studies/documents/${id}`, { ...jsonBody(patch), method: "PATCH" }) as Promise<StudyDocument>,
  confirmStudy: async (id: string) => requestJson(`/studies/documents/${id}/confirm`, { method: "POST" }) as Promise<StudyDocument>,
  retryStudy: async (id: string) => requestJson(`/studies/documents/${id}/retry`, { method: "POST" }) as Promise<StudyDocument>,
  deleteStudy: async (id: string) => requestJson(`/studies/documents/${id}`, { method: "DELETE" }),
  confirmLab: async (id: string) => requestJson(`/labs/documents/${id}/confirm`, { method: "POST" }) as Promise<LabDocument>,
  retryLab: async (id: string) => requestJson(`/labs/documents/${id}/retry`, { method: "POST" }) as Promise<LabDocument>,
  deleteLab: async (id: string) => requestJson(`/labs/documents/${id}`, { method: "DELETE" }),
  patchLabResult: async (id: string, patch: Record<string, unknown>) =>
    requestJson(`/labs/results/${id}`, { ...jsonBody(patch), method: "PATCH" }) as Promise<LabResult>,
  createLabResult: async (documentId: string, result: LabResultInput) =>
    requestJson(`/labs/documents/${documentId}/results`, jsonBody(result)) as Promise<LabResult>,
  labSummary: async (signal?: AbortSignal) => fetchJson("/labs/summary", signal) as Promise<{ items: LabResult[]; counts: Record<string, number> }>,
  labHistory: async (analyteId: string, signal?: AbortSignal) =>
    fetchJson(`/labs/analytes/${encodeURIComponent(analyteId)}/history`, signal) as Promise<{ analyte_id: string; guide: LabAnalyteGuide; items: LabResult[] }>,
  compareLabs: async (documentIds: string[]) =>
    normalizeLabCompare(await requestJson("/labs/compare", jsonBody({ document_ids: documentIds }))),
  tasks: async (state: TaskStateFilter, signal?: AbortSignal) =>
    normalizeHealthTaskList(await fetchJson(`/tasks?${new URLSearchParams({ state }).toString()}`, signal)),
  createTask: async (task: HealthTaskInput) => normalizeHealthTask(await requestJson("/tasks", jsonBody(task))),
  updateTask: async (id: string, patch: HealthTaskPatch) =>
    normalizeHealthTask(await requestJson(`/tasks/${encodeURIComponent(id)}`, { ...jsonBody(patch), method: "PATCH" })),
  completeTask: async (id: string) =>
    normalizeHealthTask(await requestJson(`/tasks/${encodeURIComponent(id)}/complete`, { method: "POST" })),
  cancelTask: async (id: string) =>
    normalizeHealthTask(await requestJson(`/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" })),
  createDoctorReport: async (period: DoctorReportPeriod, sections: DoctorReportSection[]) =>
    normalizeDoctorReport(await requestJson("/reports/doctor", jsonBody({ period, sections }))),
  doctorReport: async (id: string, signal?: AbortSignal) =>
    normalizeDoctorReport(await fetchJson(`/reports/doctor/${encodeURIComponent(id)}`, signal)),
  deleteDoctorReport: async (id: string) =>
    requestJson(`/reports/doctor/${encodeURIComponent(id)}`, { method: "DELETE" }),
  assistantMessages: async (signal?: AbortSignal) =>
    normalizeAssistantMessages(await fetchJson("/assistant/messages", signal)),
  sendAssistantMessage: async (content: string, clientRequestId: string) => {
    const message = normalizeAssistantMessage(await requestJson("/assistant/messages", jsonBody({ content, client_request_id: clientRequestId })));
    if (!message) throw new Error("Некорректный ответ ассистента");
    return message;
  },
  retryAssistantMessage: async (id: string) => {
    const message = normalizeAssistantMessage(await requestJson(`/assistant/messages/${id}/retry`, { method: "POST" }));
    if (!message) throw new Error("Некорректный ответ ассистента");
    return message;
  },
  clearAssistantHistory: async () => requestJson("/assistant/history", { method: "DELETE" }),
};

export function assistantEventsUrl(messageId: string): string {
  return `${API_ROOT}/assistant/messages/${messageId}/events`;
}

export function labDownloadUrl(documentId: string): string {
  return `${API_ROOT}/labs/documents/${documentId}/download`;
}

export function labViewUrl(documentId: string): string {
  return `${API_ROOT}/labs/documents/${documentId}/view`;
}

export function labEventsUrl(): string {
  return `${API_ROOT}/labs/events`;
}

export function studyEventsUrl(): string {
  return `${API_ROOT}/studies/events`;
}

export function studyViewUrl(documentId: string): string {
  return `${API_ROOT}/studies/documents/${documentId}/view`;
}

export function studyDownloadUrl(documentId: string): string {
  return `${API_ROOT}/studies/documents/${documentId}/download`;
}

export function csvUrl(kind: "weight" | "pressure" | "composition" | "circumference" | "activity" | "recovery", range: Period): string {
  return `${API_ROOT}/export/${kind}.csv?${new URLSearchParams({ range }).toString()}`;
}
