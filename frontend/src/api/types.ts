export type Period = "program" | "30d" | "90d" | "1y" | "all";

export interface PlanSummary {
  startDate: string;
  startWeightKg: number;
  targetWeightKg: number;
  targetDate: string | null;
  plannedTodayKg: number | null;
}

export interface WeightSummary {
  latestKg: number | null;
  latestAt: string | null;
  smoothed7dKg: number | null;
  changeSinceStartKg: number | null;
  deviationFromPlanKg: number | null;
  progressPct: number | null;
  trend28dKg: number | null;
  trend42dKg: number | null;
  forecastDate: string | null;
  measurementDays30d: number | null;
}

export interface PressureSummary {
  latestSystolic: number | null;
  latestDiastolic: number | null;
  latestPulse: number | null;
  latestAt: string | null;
  avg7dSystolic: number | null;
  avg7dDiastolic: number | null;
  avg30dSystolic: number | null;
  avg30dDiastolic: number | null;
}

export interface CompositionSummary {
  fatPct: number | null;
  fatMassKg: number | null;
  leanMassKg: number | null;
  measuredAt: string | null;
}

export type SyncStatus = "ok" | "syncing" | "delayed" | "error" | "unknown";

export interface SyncSummary {
  status: SyncStatus;
  lastSuccessAt: string | null;
  nextSyncAt: string | null;
  source: string;
}

export type InsightTone = "positive" | "attention" | "neutral" | "achievement";

export interface Insight {
  id: string;
  title: string;
  text: string;
  tone: InsightTone;
  createdAt: string | null;
}

export interface Overview {
  generatedAt: string | null;
  plan: PlanSummary;
  weight: WeightSummary;
  pressure: PressureSummary;
  composition: CompositionSummary;
  sync: SyncSummary;
  insights: Insight[];
}

export interface SeriesMeta {
  range: Period;
  from: string | null;
  to: string | null;
  count: number;
  timezone: string;
}

export interface WeightPoint {
  measuredAt: string;
  weightKg: number;
  smoothed7dKg: number | null;
  plannedKg: number | null;
  forecastKg: number | null;
  forecastLowKg: number | null;
  forecastHighKg: number | null;
  isOutlier: boolean;
}

export interface WeightProjectionPoint {
  measuredAt: string;
  forecastKg: number;
  forecastLowKg: number | null;
  forecastHighKg: number | null;
}

export interface WeightPlanPoint {
  measuredAt: string;
  plannedKg: number;
}

export interface WeeklyWeightPoint {
  startDate: string;
  endDate: string;
  actualAvgKg: number | null;
  actualMinKg: number | null;
  plannedAvgKg: number | null;
  actualChangeKg: number | null;
  plannedChangeKg: number | null;
  deviationFromPlanKg: number | null;
  measurementDays: number;
  sampleCount: number;
  outlierDays: number;
  isPartial: boolean;
}

export interface PressurePoint {
  measuredAt: string;
  systolic: number;
  diastolic: number;
  pulse: number | null;
  pulsePressure: number | null;
  sessionSize: number;
  periodOfDay: "morning" | "evening" | "other";
}

export interface CompositionPoint {
  measuredAt: string;
  fatPct: number | null;
  fatMassKg: number | null;
  leanMassKg: number | null;
}

export interface SeriesResponse<T> {
  points: T[];
  meta: SeriesMeta;
}

export interface WeightSeriesResponse extends SeriesResponse<WeightPoint> {
  projection: WeightProjectionPoint[];
  planProjection: WeightPlanPoint[];
  weekly: WeeklyWeightPoint[];
}

export interface PressureStats {
  avgSystolic: number | null;
  avgDiastolic: number | null;
  avgPulse: number | null;
  minSystolic: number | null;
  maxSystolic: number | null;
  minDiastolic: number | null;
  maxDiastolic: number | null;
  variabilitySystolic: number | null;
  variabilityDiastolic: number | null;
  sessions: number;
}

export interface PressureSeriesResponse extends SeriesResponse<PressurePoint> {
  stats7d: PressureStats;
  stats30d: PressureStats;
}

export interface ActivityPoint {
  measuredAt: string;
  steps: number | null;
  distanceKm: number | null;
  activeCaloriesKcal: number | null;
  totalCaloriesKcal: number | null;
  activeMinutes: number | null;
  workoutMinutes: number | null;
  workouts: number;
}

export interface WeeklyActivityPoint {
  startDate: string;
  endDate: string;
  actualSteps: number | null;
  baselineSteps: number | null;
  actualActiveMinutes: number | null;
  baselineActiveMinutes: number | null;
  actualWorkoutMinutes: number | null;
  workouts: number;
  coverageDays: number;
  isPartial: boolean;
}

export interface ActivitySummary {
  latestDate: string | null;
  steps: number | null;
  baselineSteps: number | null;
  distanceKm: number | null;
  activeCaloriesKcal: number | null;
  activeMinutes: number | null;
  workouts7d: number;
  dataAsOf: string | null;
}

export interface HealthCorrelation {
  metric: string;
  target: string;
  coefficient: number;
  fullOverlappingWeeks: number;
  disclaimer: string;
}

export interface ActivitySeriesResponse extends SeriesResponse<ActivityPoint> {
  weekly: WeeklyActivityPoint[];
  summary: ActivitySummary;
  correlations: HealthCorrelation[];
}

export interface RecoveryPoint {
  measuredAt: string;
  sleepMinutes: number | null;
  deepSleepMinutes: number | null;
  remSleepMinutes: number | null;
  awakeMinutes: number | null;
  restingHeartRateBpm: number | null;
  averageHeartRateBpm: number | null;
  minimumHeartRateBpm: number | null;
  maximumHeartRateBpm: number | null;
  hrvRmssdMs: number | null;
  spo2Pct: number | null;
  vo2Max: number | null;
}

export interface RecoverySummary {
  latestDate: string | null;
  sleepMinutes: number | null;
  baselineSleepMinutes: number | null;
  averageHeartRateBpm: number | null;
  minimumHeartRateBpm: number | null;
  maximumHeartRateBpm: number | null;
  restingHeartRateBpm: number | null;
  baselineRestingHeartRateBpm: number | null;
  hrvRmssdMs: number | null;
  baselineHrvRmssdMs: number | null;
  spo2Pct: number | null;
  dataAsOf: string | null;
}

export interface RecoverySeriesResponse extends SeriesResponse<RecoveryPoint> {
  summary: RecoverySummary;
  availableMetrics: string[];
  correlations: HealthCorrelation[];
}

export interface AiNarrativeItem {
  id: string;
  title: string;
  text: string;
  evidenceIds: string[];
}

export type AiAnalysisStatus = "fresh" | "stale" | "unavailable" | "pending";

export interface AiAnalysis {
  status: AiAnalysisStatus;
  headline: string | null;
  summary: string | null;
  insights: AiNarrativeItem[];
  recommendations: AiNarrativeItem[];
  limitations: string[];
  generatedAt: string | null;
  dataAsOf: string | null;
  model: string | null;
}

export interface AuthSession {
  authenticated: true;
  username: string;
  expires_at: string;
}

export interface UserProfile {
  birth_date: string | null;
  reference_sex: "male" | "female" | "unspecified" | null;
  height_cm: number;
  ai_data_consent_version: string | null;
  ai_data_consent_at: string | null;
}

export type LabStatus = "within_reference" | "below_reference" | "above_reference" | "outside_reference" | "indeterminate";

export interface LabResult {
  id: string;
  document_id: string;
  analyte_id: string | null;
  analyte_name: string;
  value_numeric: number | null;
  value_text: string | null;
  comparator: string | null;
  unit: string | null;
  observed_on: string | null;
  specimen: string | null;
  method: string | null;
  reference_low: number | null;
  reference_high: number | null;
  reference_text: string | null;
  reference_source: "laboratory" | "catalog" | "user" | "none";
  laboratory_flag: string | null;
  status: LabStatus;
  verification_status: "unverified" | "verified" | "corrected";
  source_page: number | null;
  deleted: boolean;
}

export interface LabResultInput {
  analyte_name: string;
  value_numeric: number | null;
  value_text: string | null;
  comparator: "<" | "<=" | "=" | ">=" | ">" | null;
  unit: string | null;
  observed_on: string | null;
  specimen: string | null;
  method: string | null;
  reference_low: number | null;
  reference_high: number | null;
  reference_text: string | null;
  source_page?: number | null;
}

export interface LabDocument {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  status: "queued" | "processing" | "complete" | "failed";
  verified: boolean;
  page_count: number | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  result_count: number;
  extracted_text?: string | null;
  pages?: Array<{ page: number; text: string }> | null;
  results?: LabResult[];
}

export interface AssistantSegment {
  text: string;
  evidence_keys: string[];
}

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  status: "queued" | "streaming" | "validating" | "complete" | "failed";
  content: string;
  draft_segments: AssistantSegment[];
  evidence_keys: string[];
  error_code: string | null;
  created_at: string;
  updated_at: string;
}
