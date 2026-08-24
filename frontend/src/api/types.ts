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

export interface HeartRateHourlyPoint {
  measuredAt: string;
  averageBpm: number;
  minimumBpm: number;
  maximumBpm: number;
  sampleCount: number;
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
  heartRateHourly: HeartRateHourlyPoint[];
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

export interface EvidenceTarget {
  path: string | null;
  available: boolean;
}

export interface EvidenceDescriptor {
  key: string;
  kind: string;
  metric: string;
  label: string;
  value: number | string | boolean | null;
  text: string | null;
  comparator: string | null;
  unit: string | null;
  observedOn: string | null;
  period: string | null;
  rangeStart: string | null;
  rangeEnd: string | null;
  count: number | null;
  referenceLow: number | null;
  referenceHigh: number | null;
  referenceText: string | null;
  referenceStatus: string | null;
  verification: string | null;
  target: EvidenceTarget;
}

export type EvidenceMap = Record<string, EvidenceDescriptor>;

export type AiAnalysisStatus = "fresh" | "stale" | "unavailable" | "pending";

export interface AiAnalysis {
  analysisId: number | null;
  status: AiAnalysisStatus;
  headline: string | null;
  summary: string | null;
  insights: AiNarrativeItem[];
  recommendations: AiNarrativeItem[];
  limitations: string[];
  generatedAt: string | null;
  dataAsOf: string | null;
  model: string | null;
  evidence: EvidenceMap;
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

export interface LabAnalyteGuide {
  summary: string;
  why_tested: string;
  low_meaning: string;
  high_meaning: string;
  version: string;
  reviewed_on: string;
  source: "catalog" | "ai_generated" | "pending";
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
  processing_stage: "queued" | "reading" | "extracting" | "complete" | "failed";
  progress_percent: number;
  queue_position: number | null;
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

export type StudyModality = "ultrasound" | "mri" | "ct" | "xray" | "ecg" | "other";

export interface StudyDocument {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  modality: StudyModality;
  title: string | null;
  observed_on: string | null;
  status: "queued" | "processing" | "complete" | "failed";
  processing_stage: "queued" | "reading" | "structuring" | "complete" | "failed";
  progress_percent: number;
  queue_position: number | null;
  verified: boolean;
  page_count: number | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  findings: string[];
  conclusion: string | null;
  extracted_text?: string | null;
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
  evidence: EvidenceMap;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export type DataQualityRange = "30d" | "90d";
export type DataQualityDayState = "available" | "confirmed_empty" | "missing";
export type DataQualityMetricStatus = DataQualityDayState | "partial";
export type DataSourceStatus = "healthy" | "pending" | "delayed" | "error" | "not_configured";

export interface DataQualitySource {
  key: string;
  status: DataSourceStatus;
  lastSuccessAt: string | null;
  dataAsOf: string | null;
}

export interface DataQualityDay {
  date: string;
  state: DataQualityDayState;
  source: string | null;
}

export interface DataQualityCoverage {
  known: number;
  withValues: number;
  withings: number;
  miFitness: number;
  healthConnect: number;
  confirmedEmpty: number;
  missing: number;
}

export interface DataQualityMetric {
  key: string;
  family: string;
  sourcePolicy: string;
  status: DataQualityMetricStatus;
  latestDate: string | null;
  latestSource: string | null;
  observationDays: number;
  coverage: DataQualityCoverage;
  days: DataQualityDay[];
}

export interface DataQualityResponse {
  range: DataQualityRange;
  from: string;
  to: string;
  timezone: string;
  generatedAt: string | null;
  sources: DataQualitySource[];
  metrics: DataQualityMetric[];
}

export type TaskStateFilter = "open" | "completed" | "all";
export type TaskStatus = "active" | "completed" | "cancelled";
export type TaskRecurrence = "once" | "daily" | "weekly" | "monthly";

export interface HealthTaskSource {
  kind: string;
  title: string;
  text: string;
  evidenceIds: string[];
  generatedAt: string | null;
}

export interface HealthTask {
  id: string;
  title: string;
  note: string | null;
  nextDueAt: string | null;
  recurrence: TaskRecurrence;
  telegramEnabled: boolean;
  status: TaskStatus;
  overdue: boolean;
  sourceAnalysisId: number | null;
  sourceItemId: string | null;
  source: HealthTaskSource | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  cancelledAt: string | null;
}

export interface HealthTaskInput {
  title: string;
  note: string | null;
  next_due_at: string;
  recurrence: TaskRecurrence;
  telegram_enabled: boolean;
  source_analysis_id?: number;
  source_item_id?: string;
}

export interface HealthTaskPatch {
  title?: string;
  note?: string | null;
  next_due_at?: string;
  recurrence?: TaskRecurrence;
  telegram_enabled?: boolean;
}

export interface HealthTaskList {
  items: HealthTask[];
  openCount: number;
}

export interface LabComparePanel {
  documentId: string;
  observedOn: string | null;
  verified: boolean;
  resultCount: number;
}

export type LabCompareIncompatibility =
  | "missing_result"
  | "multiple_results"
  | "non_numeric_value"
  | "qualified_value"
  | "different_unit"
  | "different_specimen"
  | "different_method"
  | null;

export interface LabCompareDelta {
  fromDocumentId: string;
  toDocumentId: string;
  absolute: number;
  percent: number | null;
}

export interface LabCompareRow {
  analyteId: string | null;
  analyteName: string;
  cells: LabResult[][];
  comparable: boolean;
  incompatibility: LabCompareIncompatibility;
  deltas: LabCompareDelta[];
  missing: boolean;
  statusChanged: boolean;
  valueChanged: boolean;
}

export interface LabCompareResponse {
  panels: LabComparePanel[];
  rows: LabCompareRow[];
}

export type DoctorReportPeriod = "30d" | "90d" | "1y";
export type DoctorReportSection = "summary" | "weight" | "pressure" | "activity" | "recovery" | "labs" | "studies" | "ai";

export interface DoctorReportMeta {
  createdAt: string | null;
  period: DoctorReportPeriod;
  from: string | null;
  to: string | null;
  timezone: string;
}

export interface DoctorReportLabItem {
  analyte: string;
  value: string;
  observedOn: string | null;
  reference: string | null;
  status: LabStatus;
  verificationStatus: "verified" | "corrected";
}

export interface DoctorReportStudyItem {
  modality: StudyModality;
  observedOn: string | null;
  findings: string[];
  conclusion: string | null;
}

export interface DoctorReportAiItem {
  title: string;
  text: string;
  evidenceIds: string[];
}

export interface DoctorReportPreview {
  meta: DoctorReportMeta;
  summary: Record<string, unknown> | null;
  weight: WeightSeriesResponse | null;
  pressure: PressureSeriesResponse | null;
  activity: ActivitySeriesResponse | null;
  recovery: RecoverySeriesResponse | null;
  labs: DoctorReportLabItem[] | null;
  studies: DoctorReportStudyItem[] | null;
  ai: DoctorReportAiItem[] | null;
}

export interface DoctorReport {
  id: string;
  period: DoctorReportPeriod;
  sections: DoctorReportSection[];
  preview: DoctorReportPreview;
  pageCount: number;
  sizeBytes: number;
  createdAt: string;
  expiresAt: string;
  downloadUrl: string;
}
