import { expect, test } from "@playwright/test";

const overview = {
  generated_at: "2026-09-02T08:00:00Z",
  plan: { start_date: "2026-08-15", start_weight_kg: 127.03, target_weight_kg: 76.5, target_date: "2027-09-04", planned_today_kg: 124.707 },
  weight: { latest_kg: 125.5, latest_at: "2026-09-01T05:00:00Z", smoothed_7d_kg: 125.8, change_since_start_kg: -1.53, deviation_from_plan_kg: 0.793, progress_pct: 3.0, trend_28d_kg: -1.5, measurement_days_30d: 6 },
  pressure: { latest_systolic: 122, latest_diastolic: 78, latest_pulse: 64, latest_at: "2026-08-18T18:00:00Z" },
  composition: { fat_pct: 31.2, fat_mass_kg: 39.4, lean_mass_kg: 86.8, measured_at: "2026-08-19T05:00:00Z" },
  sync: { status: "ok", last_success_at: "2026-09-02T08:00:00Z", source: "Withings Cloud" },
  insights: [{ id: "on-plan", title: "План соблюдается", text: "Сглаженный прогресс идёт рядом с плановой линией.", tone: "positive" }],
};

const weightSeries = {
  points: [{ measured_at: "2026-09-01T05:00:00Z", weight_kg: 125.5, smoothed_7d_kg: 125.8, planned_kg: 124.9 }],
  weekly: [
    { start_date: "2026-08-15", end_date: "2026-08-16", actual_avg_kg: 127, actual_min_kg: 126.9, planned_avg_kg: 126.97, actual_change_kg: null, planned_change_kg: null, deviation_from_plan_kg: 0.03, measurement_days: 2, sample_count: 3, outlier_days: 0, is_partial: true },
    { start_date: "2026-08-17", end_date: "2026-08-23", actual_avg_kg: 126.25, actual_min_kg: 126, planned_avg_kg: 126.55, actual_change_kg: -0.75, planned_change_kg: -0.42, deviation_from_plan_kg: -0.3, measurement_days: 3, sample_count: 4, outlier_days: 1, is_partial: false },
    { start_date: "2026-08-24", end_date: "2026-08-30", actual_avg_kg: null, actual_min_kg: null, planned_avg_kg: 125.63, actual_change_kg: null, planned_change_kg: -0.92, deviation_from_plan_kg: null, measurement_days: 0, sample_count: 0, outlier_days: 0, is_partial: false },
    { start_date: "2026-08-31", end_date: "2026-09-02", actual_avg_kg: 125.5, actual_min_kg: 125.5, planned_avg_kg: 124.9, actual_change_kg: null, planned_change_kg: -0.73, deviation_from_plan_kg: 0.6, measurement_days: 1, sample_count: 1, outlier_days: 0, is_partial: true },
  ],
  projection: [],
  plan_projection: [],
  meta: { range: "program", count: 1 },
};

const activitySeries = {
  data_as_of: "2026-09-02T08:00:00Z",
  daily: [
    { date: "2026-09-01", steps: 8432, distance_km: 6.2, active_minutes: 47, active_calories_kcal: 510, workouts: 1 },
    { date: "2026-09-02", steps: 6120, distance_km: 4.4, active_minutes: 31, active_calories_kcal: 360, workouts: 0 },
  ],
  weekly: [{ start_date: "2026-08-31", end_date: "2026-09-06", actual_steps: 14552, baseline_steps: 13200, coverage_days: 2, is_partial: true }],
  summary: { steps: 6120, baseline_steps: 5900, distance_km: 4.4, active_minutes: 31, workouts_7d: 2, data_as_of: "2026-09-02T08:00:00Z" },
  correlations: [
    { metric: "steps", target: "weight_kg", coefficient: -0.2, full_overlapping_weeks: 20, disclaimer: "Корреляция не доказывает причинность." },
    { metric: "active_minutes", target: "systolic_mm_hg", coefficient: -0.4, full_overlapping_weeks: 16, disclaimer: "Корреляция не доказывает причинность." },
  ],
  meta: { range: "90d", count: 2 },
};

const recoverySeries = {
  data_as_of: "2026-09-02T08:00:00Z",
  daily: [
    { date: "2026-09-01", sleep_minutes: 438, deep_sleep_minutes: 91, rem_sleep_minutes: 102, resting_heart_rate_bpm: 62, hrv_rmssd_ms: 43, spo2_pct: 97.1 },
    { date: "2026-09-02", sleep_minutes: 461, deep_sleep_minutes: 98, rem_sleep_minutes: 110, resting_heart_rate_bpm: 60, hrv_rmssd_ms: 46, spo2_pct: 97.4 },
  ],
  heart_rate_hourly: [
    { measured_at: "2026-08-31T21:00:00Z", average_bpm: 71, minimum_bpm: 58, maximum_bpm: 91, sample_count: 6 },
    { measured_at: "2026-08-31T22:00:00Z", average_bpm: 68, minimum_bpm: 55, maximum_bpm: 88, sample_count: 12 },
    { measured_at: "2026-08-31T23:00:00Z", average_bpm: 65, minimum_bpm: 52, maximum_bpm: 84, sample_count: 6 },
    { measured_at: "2026-09-01T00:00:00Z", average_bpm: 64, minimum_bpm: 51, maximum_bpm: 82, sample_count: 6 },
    { measured_at: "2026-09-02T09:00:00Z", average_bpm: 78, minimum_bpm: 62, maximum_bpm: 101, sample_count: 10 },
    { measured_at: "2026-09-02T10:00:00Z", average_bpm: 82, minimum_bpm: 66, maximum_bpm: 107, sample_count: 14 },
  ],
  summary: { sleep_minutes: 461, baseline_sleep_minutes: 445, resting_heart_rate_bpm: 60, baseline_resting_heart_rate_bpm: 62, hrv_rmssd_ms: 46, baseline_hrv_rmssd_ms: 42, spo2_pct: 97.4, data_as_of: "2026-09-02T08:00:00Z" },
  available_metrics: ["sleep", "resting_heart_rate", "hrv", "spo2"],
  correlations: [
    { metric: "resting_heart_rate_bpm", target: "weight_kg", coefficient: 0.3, full_overlapping_weeks: 8, disclaimer: "Корреляция не доказывает причинность." },
  ],
  meta: { range: "90d", count: 2 },
};

const session = { authenticated: true, username: "amigo", expires_at: "2026-12-01T00:00:00Z" };
const profile = {
  birth_date: "1988-04-12",
  reference_sex: "male",
  height_cm: 176,
  ai_data_consent_version: "amigo-ai-data-v1",
  ai_data_consent_at: "2026-09-01T08:00:00Z",
};
const labResult = {
  id: "10000000-0000-0000-0000-000000000001",
  document_id: "20000000-0000-0000-0000-000000000001",
  analyte_id: "ferritin",
  analyte_name: "Ферритин",
  value_numeric: 42,
  value_text: null,
  comparator: "=",
  unit: "нг/мл",
  observed_on: "2026-08-28",
  specimen: "serum",
  method: null,
  reference_low: 30,
  reference_high: 400,
  reference_text: null,
  reference_source: "laboratory",
  laboratory_flag: null,
  status: "within_reference",
  verification_status: "verified",
  source_page: 1,
  deleted: false,
};
const labDocument = {
  id: labResult.document_id,
  filename: "анализы.pdf",
  media_type: "application/pdf",
  size_bytes: 104857,
  status: "complete",
  verified: true,
  page_count: 1,
  error_code: null,
  created_at: "2026-08-28T09:00:00Z",
  completed_at: "2026-08-28T09:01:00Z",
  result_count: 1,
};
const secondLabDocument = {
  ...labDocument,
  id: "20000000-0000-0000-0000-000000000002",
  filename: "анализы-сентябрь.pdf",
  created_at: "2026-09-01T09:00:00Z",
  completed_at: "2026-09-01T09:01:00Z",
  verified: false,
};
const secondLabResult = {
  ...labResult,
  id: "10000000-0000-0000-0000-000000000004",
  document_id: secondLabDocument.id,
  value_numeric: 48,
  observed_on: "2026-09-01",
  verification_status: "unverified",
};
const studyDocument = {
  id: "40000000-0000-0000-0000-000000000001",
  filename: "mri-report.pdf",
  media_type: "application/pdf",
  size_bytes: 125000,
  modality: "mri",
  title: "МРТ коленного сустава",
  observed_on: "2026-08-30",
  status: "complete",
  processing_stage: "complete",
  progress_percent: 100,
  queue_position: null,
  verified: true,
  page_count: 2,
  error_code: null,
  created_at: "2026-08-30T09:00:00Z",
  completed_at: "2026-08-30T09:01:00Z",
  findings: ["Суставные поверхности без видимых изменений."],
  conclusion: "Значимых изменений не выявлено.",
  extracted_text: "Описание исследования без идентификаторов.",
};
const assistantRecommendation = {
  id: "recommendation-1",
  title: "Сверить динамику",
  text: "Сопоставьте ферритин со следующим плановым анализом.",
  evidence_ids: ["lab.ferritin.latest"],
};

test.beforeEach(async ({ page }) => {
  let assistantSent = false;
  let taskItems: Array<Record<string, unknown>> = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith("/auth/session")) return route.fulfill({ json: session });
    if (path.endsWith("/auth/login")) return route.fulfill({ json: session });
    if (path.endsWith("/auth/logout")) return route.fulfill({ status: 204 });
    if (path.endsWith("/profile")) return route.fulfill({ json: profile });
    if (path.endsWith("/data-quality")) return route.fulfill({ json: {
      range: new URL(route.request().url()).searchParams.get("range") ?? "30d",
      from: "2026-08-31", to: "2026-09-01", timezone: "Europe/Moscow", generated_at: "2026-09-02T08:00:00Z",
      sources: {
        withings: { status: "healthy", last_success_at: "2026-09-02T07:55:00Z", data_as_of: "2026-09-02T07:55:00Z" },
        mi_fitness: { status: "healthy", last_success_at: "2026-09-02T07:50:00Z", data_as_of: "2026-09-01T21:00:00Z" },
        health_connect: { status: "delayed", last_success_at: "2026-09-01T08:00:00Z", data_as_of: "2026-09-01T07:00:00Z" },
      },
      metrics: [
        { key: "weight", family: "weight", source_policy: "withings_only", status: "partial", days: [{ date: "2026-08-31", state: "available", source: "withings" }, { date: "2026-09-01", state: "missing", source: null }] },
        { key: "steps", family: "activity", source_policy: "xiaomi_finalized_only", status: "partial", days: [{ date: "2026-08-31", state: "confirmed_empty", source: "mi_fitness" }, { date: "2026-09-01", state: "available", source: "mi_fitness" }] },
      ],
    } });
    if (path.endsWith("/tasks") && method === "GET") {
      const state = new URL(route.request().url()).searchParams.get("state") ?? "open";
      const items = state === "open" ? taskItems.filter((item) => item.status === "active") : state === "completed" ? taskItems.filter((item) => item.status === "completed") : taskItems;
      return route.fulfill({ json: { items, open_count: taskItems.filter((item) => item.status === "active").length } });
    }
    if (path.endsWith("/tasks") && method === "POST") {
      const payload = route.request().postDataJSON();
      expect(payload).toMatchObject({ source_analysis_id: 42, source_item_id: "recommendation-1", telegram_enabled: true });
      const task = { id: "task-1", ...payload, status: "active", overdue: false, source: { kind: "ai_recommendation", title: "Сохранить ритм", text: "Поддерживайте текущую регулярность прогулок.", evidence_ids: ["activity.steps.week"], generated_at: "2026-09-02T07:55:00Z" }, created_at: "2026-09-02T08:00:00Z", updated_at: "2026-09-02T08:00:00Z", completed_at: null, cancelled_at: null };
      taskItems = [task];
      return route.fulfill({ status: 201, json: task });
    }
    if (path.endsWith("/tasks/task-1") && method === "PATCH") {
      taskItems = [{ ...taskItems[0], ...route.request().postDataJSON(), updated_at: "2026-09-02T08:05:00Z" }];
      return route.fulfill({ json: taskItems[0] });
    }
    if (path.endsWith("/tasks/task-1/complete") && method === "POST") {
      taskItems = [{ ...taskItems[0], status: "completed", next_due_at: null, completed_at: "2026-09-02T08:10:00Z" }];
      return route.fulfill({ json: taskItems[0] });
    }
    if (path.endsWith("/tasks/task-1/cancel") && method === "POST") {
      taskItems = [{ ...taskItems[0], status: "cancelled", next_due_at: null, cancelled_at: "2026-09-02T08:10:00Z" }];
      return route.fulfill({ json: taskItems[0] });
    }
    if (path.endsWith("/labs/compare") && method === "POST") {
      expect(route.request().postDataJSON()).toEqual({ document_ids: [labDocument.id, secondLabDocument.id] });
      return route.fulfill({ json: {
        panels: [{ document_id: labDocument.id, observed_on: "2026-08-28", verified: true, result_count: 1 }, { document_id: secondLabDocument.id, observed_on: "2026-09-01", verified: false, result_count: 1 }],
        rows: [{ analyte_id: "ferritin", analyte_name: "Ферритин", cells: [[labResult], [secondLabResult]], comparable: true, incompatibility: null, deltas: [{ from_document_id: labDocument.id, to_document_id: secondLabDocument.id, absolute: 6, percent: 14.29 }], missing: false, status_changed: false, value_changed: true }],
      } });
    }
    if (path.endsWith("/reports/doctor") && method === "POST") {
      const payload = route.request().postDataJSON();
      expect(payload.period).toBe("90d");
      expect(payload.sections).not.toContain("ai");
      return route.fulfill({ status: 201, json: {
        id: "report-1", options: payload, page_count: 3, size_bytes: 124000, created_at: "2026-09-02T08:00:00Z", expires_at: "2026-09-03T08:00:00Z", download_url: "/amigo/api/v1/reports/doctor/report-1.pdf",
        preview: { meta: { created_at: "2026-09-02T08:00:00Z", period: "90d", from: "2026-06-06", to: "2026-09-02", timezone: "Europe/Moscow" }, sections: {
          summary: { height_cm: 176, weight: { latest_kg: 125.5 }, pressure: { latest_systolic: 122, latest_diastolic: 78 } },
          weight: weightSeries,
          pressure: { points: [{ measured_at: "2026-08-18T18:00:00Z", systolic: 122, diastolic: 78, pulse: 64, pulse_pressure: 44, session_size: 2, period_of_day: "evening" }] },
          activity: activitySeries,
          recovery: recoverySeries,
          labs: [{ analyte: "Ферритин", value: "42 нг/мл", observed_on: "2026-08-28", reference: "30–400 нг/мл", status: "within_reference", verification_status: "verified" }],
          studies: [{ modality: "mri", observed_on: "2026-08-30", findings: ["Суставные поверхности без видимых изменений."], conclusion: "Значимых изменений не выявлено." }],
        } },
      } });
    }
    if (path.endsWith("/reports/doctor/report-1.pdf") && method === "GET") return route.fulfill({ status: 200, contentType: "application/pdf", headers: { "Content-Disposition": "attachment; filename=amigo-doctor-report.pdf" }, body: "%PDF-1.4 synthetic doctor report" });
    if (path.endsWith("/reports/doctor/report-1") && method === "DELETE") return route.fulfill({ status: 204 });
    if (path.endsWith("/labs/summary")) return route.fulfill({ json: {
      items: [labResult],
      counts: { within_reference: 1, below_reference: 0, above_reference: 0, outside_reference: 0, indeterminate: 0 },
    } });
    if (path.endsWith("/labs/documents") && method === "GET") return route.fulfill({ json: { items: [labDocument, secondLabDocument] } });
    if (path.endsWith("/labs/uploads") && method === "POST") return route.fulfill({ status: 202, json: { ...labDocument, status: "queued", verified: false } });
    if (path.endsWith("/studies/documents") && method === "GET") return route.fulfill({ json: { items: [studyDocument] } });
    if (path.endsWith(`/studies/documents/${studyDocument.id}`)) return route.fulfill({ json: studyDocument });
    if (path.includes("/export/") && path.endsWith(".csv")) return route.fulfill({
      status: 200,
      contentType: "text/csv; charset=utf-8",
      headers: { "Content-Disposition": "attachment; filename=amigo-weight.csv" },
      body: "measured_at,value,unit\n2026-09-01T05:00:00Z,125.5,kg\n",
    });
    if (path.endsWith(`/labs/documents/${labDocument.id}/download`)) return route.fulfill({
      status: 200,
      contentType: "application/pdf",
      headers: { "Content-Disposition": "attachment; filename*=UTF-8''%D0%B0%D0%BD%D0%B0%D0%BB%D0%B8%D0%B7%D1%8B.pdf" },
      body: "%PDF-1.4 synthetic original",
    });
    if (path.endsWith(`/labs/documents/${labDocument.id}/results`) && method === "POST") {
      expect(route.request().postDataJSON()).toMatchObject({
        analyte_name: "Трансферрин",
        value_numeric: 2.7,
        unit: "г/л",
        observed_on: "2026-08-28",
      });
      return route.fulfill({ status: 201, json: { ...labResult, id: "10000000-0000-0000-0000-000000000002", analyte_id: "transferrin", analyte_name: "Трансферрин", value_numeric: 2.7, unit: "г/л", verification_status: "corrected" } });
    }
    if (path.endsWith(`/labs/documents/${labDocument.id}`)) return route.fulfill({ json: { ...labDocument, extracted_text: "Ферритин 42 нг/мл", pages: [{ page: 1, text: "Ферритин 42 нг/мл" }], results: [labResult] } });
    if (path.endsWith("/labs/analytes/ferritin/history")) return route.fulfill({ json: { analyte_id: "ferritin", items: [
      labResult,
      { ...labResult, id: "10000000-0000-0000-0000-000000000003", observed_on: "2026-08-29", value_numeric: 7.5, unit: "мкмоль/л", reference_low: 5, reference_high: 10 },
    ] } });
    if (path.endsWith("/assistant/messages") && method === "POST") {
      assistantSent = true;
      return route.fulfill({ status: 202, json: {
        id: "30000000-0000-0000-0000-000000000002", role: "assistant", status: "queued", content: "",
        draft_segments: [], evidence_keys: [], error_code: null,
        created_at: "2026-09-02T08:00:00Z", updated_at: "2026-09-02T08:00:00Z",
      } });
    }
    if (path.endsWith("/assistant/messages") && method === "GET") return route.fulfill({ json: {
      items: assistantSent ? [
        { id: "30000000-0000-0000-0000-000000000001", role: "user", status: "complete", content: "Что с ферритином?", draft_segments: [], evidence_keys: [], error_code: null, created_at: "2026-09-02T08:00:00Z", updated_at: "2026-09-02T08:00:00Z" },
        { id: "30000000-0000-0000-0000-000000000002", role: "assistant", status: "complete", content: "Ферритин находится в референсе бланка.", draft_segments: [], evidence_keys: ["lab.ferritin.latest"], evidence: { "lab.ferritin.latest": { kind: "laboratory_result", metric: "laboratory", label: "Ферритин", value_numeric: 42, unit: "нг/мл", observed_on: "2026-08-28", verification: "verified", target: { path: `/labs/documents/${labDocument.id}#result-${labResult.id}`, available: true } } }, error_code: null, created_at: "2026-09-02T08:00:01Z", updated_at: "2026-09-02T08:00:02Z" },
      ] : [],
      analysis_id: 42,
      recommendations: [assistantRecommendation],
      evidence: { "lab.ferritin.latest": { key: "lab.ferritin.latest", kind: "laboratory", metric: "laboratory", label: "Ферритин", value: 42, unit: "нг/мл", date: "2026-08-28", verification: "verified", target: { path: `/labs/documents/${labDocument.id}#result-${labResult.id}`, available: true } } },
    } });
    if (path.includes("/assistant/messages/") && path.endsWith("/events")) return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "event: draft_segment\ndata: {\"text\":\"Проверяю историю ферритина…\",\"evidence_keys\":[\"lab.ferritin.latest\"]}\n\nevent: complete\ndata: {}\n\n",
    });
    if (path.endsWith("/overview")) return route.fulfill({ json: overview });
    if (path.endsWith("/ai-analysis")) return route.fulfill({ json: {
      analysis_id: 42,
      status: "fresh",
      headline: "Динамика остаётся управляемой",
      summary: "Активность выше личной базы, а последние ночи сна стали немного длиннее.",
      insights: [{ id: "observation-1", title: "Больше движения", text: "Недельная активность выше вашей личной базы.", evidence_ids: ["activity.steps.week"] }],
      recommendations: [{ id: "recommendation-1", title: "Сохранить ритм", text: "Поддерживайте текущую регулярность прогулок.", evidence_ids: ["activity.steps.week"] }],
      limitations: ["Текущая неделя ещё не завершена"],
      generated_at: "2026-09-02T07:55:00Z",
      data_as_of: "2026-09-02T07:50:00Z",
      model: "gpt-5.6-sol",
      prompt_version: "amigo-health-v4",
      evidence: { "activity.steps.week": { key: "activity.steps.week", kind: "series", metric: "activity", label: "Активность Xiaomi Cloud", unit: "steps", range: { from: "2026-08-24", to: "2026-09-01" }, count: 7, target: { path: "/activity", available: true } } },
    } });
    if (path.endsWith("/insights")) return route.fulfill({ json: { items: overview.insights } });
    if (path.endsWith("/series/pressure")) {
      return route.fulfill({ json: { points: [
        { measured_at: "2026-08-18T12:00:00Z", systolic: 181, diastolic: 80, pulse: 68, pulse_pressure: 101, session_size: 1, period_of_day: "other" },
        { measured_at: "2026-08-18T18:00:00Z", systolic: 122, diastolic: 78, pulse: 64, pulse_pressure: 44, session_size: 2, period_of_day: "evening" },
      ], meta: { range: "90d", count: 2 } } });
    }
    if (path.endsWith("/series/activity")) return route.fulfill({ json: activitySeries });
    if (path.endsWith("/series/recovery")) return route.fulfill({ json: recoverySeries });
    if (path.endsWith("/series/weight")) return route.fulfill({ json: weightSeries });
    return route.fulfill({ json: { points: [{ measured_at: "2026-08-19T05:00:00Z", weight_kg: 126.2, smoothed_7d_kg: 126.4, planned_kg: 126.5 }], meta: { range: "90d", count: 1 } } });
  });
});

test("renders the overview and navigates to pressure", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Добрый день! Вот как идут дела" })).toBeVisible();
  await expect(page.getByLabel("Главные показатели").getByText("125,5 кг")).toBeVisible();
  await page.getByRole("link", { name: "Давление", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Статистика давления" })).toBeVisible();
  await expect(page.getByText("122 / 78").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Дневной визуальный ориентир" })).toBeVisible();
  await expect(page.getByRole("img", { name: /Дневная полоса категорий давления/ })).toBeVisible();
  await expect(page.getByRole("list", { name: "Границы категорий давления" })).toContainText("сист. ≥ 180 или диаст. ≥ 120");
  await expect(page.getByText(/Это визуальный ориентир, а не диагноз/)).toBeVisible();
  await page.getByText("Показать дневные категории (1)").click();
  await expect(page.getByRole("table", { name: "Дневные категории давления и диапазоны сессий" })).toContainText("Критически высокое");
});

test("renders weekly plan/fact charts and their accessible table", async ({ page }) => {
  await page.goto("./");
  await page.getByRole("link", { name: "Прогресс", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Вес по неделям" })).toBeVisible();
  await expect(page.getByRole("img", { name: /Недельный график среднего фактического и планового веса/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Изменение по неделям" })).toBeVisible();
  await expect(page.getByText(/Отрицательное — снижение/)).toBeVisible();

  await page.getByText("Показать недельную таблицу (4)").click();
  const table = page.getByRole("table", { name: "Недельные показатели веса относительно плана" });
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Факт, средний" })).toBeVisible();
  await expect(table.getByText("Нет замеров")).toBeVisible();
});

test("renders AI analysis, activity baseline and recovery", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "ИИ-анализ" })).toBeVisible();
  await expect(page.getByText("Динамика остаётся управляемой")).toBeVisible();

  await page.getByRole("link", { name: "Активность", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Активность", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Недели: факт и личная база" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Совместная динамика" })).toBeVisible();
  await expect(page.getByText(/r — коэффициент линейной корреляции Пирсона от −1 до 1/)).toBeVisible();
  await expect(page.getByText("Слабая обратная линейная связь.")).toBeVisible();

  await page.getByRole("link", { name: "Восстановление", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Восстановление", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Сон", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Пульс с часов" })).toBeVisible();
  await expect(page.getByRole("button", { name: "1 ч" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/Пропуски данных показаны разрывами/)).toBeVisible();
  await expect(page.getByText("Слабая прямая линейная связь.")).toBeVisible();
  await expect(
    page.getByRole("article").filter({ hasText: "Последний сон" }).getByText("7 ч 41 мин", { exact: true }),
  ).toBeVisible();
});

test("persists one chart period across reloads and chart pages", async ({ page }) => {
  await page.goto("./activity");
  const activityPeriod = page.getByRole("group", { name: "Период графика" });
  await activityPeriod.getByRole("button", { name: "Год", exact: true }).click();
  await expect(activityPeriod.getByRole("button", { name: "Год", exact: true })).toHaveAttribute("aria-pressed", "true");

  await page.reload();
  await expect(
    page.getByRole("group", { name: "Период графика" }).getByRole("button", { name: "Год", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("link", { name: "Давление", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Период графика" }).getByRole("button", { name: "Год", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("keeps the light default and a usable persistent theme selector on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("./");

  const selector = page.getByRole("combobox", { name: "Тема оформления" });
  await expect(selector).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await selector.selectOption("sunset");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "sunset");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Тема оформления" })).toHaveValue("sunset");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "sunset");
});

test("keeps narrow mobile layouts usable and the active navigation item visible", async ({ page }) => {
  for (const width of [320, 360, 412]) {
    await page.setViewportSize({ width, height: 780 });
    await page.goto("./labs/documents/20000000-0000-0000-0000-000000000001");
    await expect(page.getByRole("heading", { name: "анализы.pdf" })).toBeVisible();
    const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(hasOverflow).toBe(false);
    const active = page.locator(".main-nav a.active");
    const bounds = await active.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
  }
});

test("keeps the new chart controls, pressure guide and correlation cards inside a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 780 });
  for (const path of ["./activity", "./recovery", "./pressure"]) {
    await page.goto(path);
    const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(hasOverflow).toBe(false);
  }
});

test("keeps a protected deep link through login", async ({ page }) => {
  let authenticated = false;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/session") && !authenticated) {
      return route.fulfill({ status: 401, json: { detail: "authentication_required" } });
    }
    if (path.endsWith("/auth/login")) {
      expect(route.request().postDataJSON()).toEqual({ username: "amigo", password: "long-local-password" });
      authenticated = true;
      return route.fulfill({ json: session });
    }
    return route.fallback();
  });

  await page.goto("./labs");
  await expect(page.getByRole("heading", { name: "Вход в Amigo" })).toBeVisible();
  await page.getByLabel("Пароль").fill("long-local-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/amigo\/labs$/);
  await expect(page.getByRole("heading", { name: "Результаты анализов" })).toBeVisible();
});

test("shows laboratory summary and upload queue", async ({ page }) => {
  await page.goto("./labs");
  await expect(page.getByRole("heading", { name: "Результаты анализов" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Ферритин" })).toContainText("42 нг/мл");
  await expect(page.getByText("В референсе").first()).toBeVisible();

  await page.getByRole("link", { name: "Загрузить анализы" }).click();
  await expect(page.getByRole("heading", { name: "Загрузка и обработка" })).toBeVisible();
  await page.getByLabel("Выбрать файл").setInputFiles({
    name: "new-labs.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 synthetic e2e fixture"),
  });
  await expect(page.getByRole("status")).toContainText("1 из 1 файлов приняты и поставлены в очередь");
  await expect(page.getByText("анализы.pdf")).toBeVisible();

  await page.locator("article.document-row").filter({ hasText: "анализы.pdf" }).getByRole("link", { name: "Результаты" }).click();
  await page.getByRole("button", { name: "Добавить показатель" }).click();
  const form = page.locator("form.add-result-form");
  await form.getByLabel("Показатель").fill("Трансферрин");
  await form.getByLabel("Число").fill("2.7");
  await form.getByLabel("Единица").fill("г/л");
  await form.getByLabel("Дата").fill("2026-08-28");
  await form.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByRole("button", { name: "Добавить показатель" })).toBeVisible();
});

test("downloads authenticated CSV and a laboratory original", async ({ page }) => {
  await page.goto("./");
  const csvLink = page.getByRole("link", { name: "Скачать CSV" });
  // Chromium's native download attribute bypasses Playwright request routing;
  // the production Content-Disposition header remains what names the file.
  await csvLink.evaluate((element) => element.removeAttribute("download"));
  const csvPromise = page.waitForEvent("download");
  await csvLink.click();
  const csv = await csvPromise;
  expect(csv.suggestedFilename()).toBe("amigo-weight.csv");

  await page.goto(`./labs/documents/${labDocument.id}`);
  const originalLink = page.getByRole("link", { name: "Скачать", exact: true });
  const originalPromise = page.waitForEvent("download");
  await originalLink.click();
  const original = await originalPromise;
  expect(original.suggestedFilename()).toBe("анализы.pdf");
});

test("revokes the session from the mobile profile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("./profile");
  const logoutRequest = page.waitForRequest((request) => request.url().endsWith("/auth/logout"));
  await page.getByRole("button", { name: "Выйти из Amigo" }).click();
  expect((await logoutRequest).method()).toBe("POST");
  await expect(page.getByRole("heading", { name: "Вход в Amigo" })).toBeVisible();
});

test("separates laboratory history charts by unit", async ({ page }) => {
  await page.goto("./labs");
  await page.getByRole("link", { name: "Ферритин" }).click();

  await expect(page.getByRole("img", { name: "Динамика показателя в единицах нг/мл" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Динамика показателя в единицах мкмоль/л" })).toBeVisible();
});

test("opens the structured studies archive", async ({ page }) => {
  await page.goto("./studies");
  await expect(page.getByRole("heading", { name: "Исследования", exact: true })).toBeVisible();
  const row = page.locator("article.document-row").filter({ hasText: "МРТ коленного сустава" });
  await expect(row).toContainText("МРТ");
  await row.getByRole("link", { name: "Заключение" }).click();
  await expect(page.getByRole("heading", { name: "МРТ коленного сустава" })).toBeVisible();
  await expect(page.getByText("Значимых изменений не выявлено.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Посмотреть оригинал" })).toBeVisible();
});

test("edits privacy profile and renders the persistent assistant", async ({ page }) => {
  await page.goto("./profile");
  await expect(page.getByRole("heading", { name: "Профиль и приватность" })).toBeVisible();
  await expect(page.getByLabel("Дата рождения")).toHaveValue("1988-04-12");
  await expect(page.getByRole("checkbox")).toBeChecked();
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByRole("status")).toHaveText("Профиль сохранён.");

  await page.getByRole("link", { name: "Ассистент", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Ассистент здоровья" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Актуальные рекомендации" })).toBeVisible();
  const currentRecommendation = page.locator("article.insight--recommendation").filter({ hasText: "Сверить динамику" });
  await currentRecommendation.getByRole("button", { name: "Ферритин" }).click();
  await expect(page.getByRole("dialog", { name: "Ферритин" })).toContainText("зафиксировано в момент анализа");
  await page.getByRole("button", { name: "Закрыть основание" }).click();
  await currentRecommendation.getByRole("button", { name: "Создать задачу" }).click();
  const taskDialog = page.getByRole("dialog", { name: "Создать задачу" });
  await expect(taskDialog.getByLabel("Название")).toHaveValue("Сверить динамику");
  await taskDialog.getByLabel("Дата и время").fill("2027-09-03T09:00");
  await taskDialog.getByRole("button", { name: "Создать задачу" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Задача создана и доступна в разделе «Задачи»." })).toBeVisible();
  await page.getByPlaceholder("Задайте вопрос по вашим данным…").fill("Что с ферритином?");
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByText("Ферритин находится в референсе бланка.")).toBeVisible();
  const assistantMessage = page.locator("article.chat-message--assistant").filter({ hasText: "Ферритин находится" });
  await assistantMessage.getByRole("button", { name: "Ферритин" }).click();
  await expect(page.getByRole("dialog", { name: "Ферритин" })).toContainText("зафиксировано в момент анализа");
  await page.getByRole("button", { name: "Закрыть основание" }).click();
  await expect(page.getByText(/не предназначен для экстренной оценки/i)).toBeVisible();
});

test("opens immutable evidence and creates, edits and completes a task", async ({ page }) => {
  await page.goto("./");
  const recommendation = page.locator("article.insight--recommendation").filter({ hasText: "Сохранить ритм" });
  await recommendation.getByRole("button", { name: "Активность Xiaomi Cloud" }).click();
  const drawer = page.getByRole("dialog", { name: "Активность Xiaomi Cloud" });
  await expect(drawer).toContainText("зафиксировано в момент анализа");
  await expect(drawer.getByRole("link", { name: "Открыть исходные данные" })).toHaveAttribute("href", "/amigo/activity");
  await drawer.getByRole("button", { name: "Закрыть основание" }).click();

  await recommendation.getByRole("button", { name: "Создать задачу" }).click();
  const createDialog = page.getByRole("dialog", { name: "Создать задачу" });
  await expect(createDialog.getByLabel("Название")).toHaveValue("Сохранить ритм");
  await createDialog.getByLabel("Дата и время").fill("2027-09-03T09:00");
  await createDialog.getByRole("button", { name: "Создать задачу" }).click();

  await page.getByRole("link", { name: "Задачи", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Сохранить ритм" })).toBeVisible();
  await page.getByRole("button", { name: "Изменить" }).click();
  const editDialog = page.getByRole("dialog", { name: "Изменить задачу" });
  await editDialog.getByLabel("Название").fill("Ежедневная прогулка");
  await editDialog.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByRole("heading", { name: "Ежедневная прогулка" })).toBeVisible();
  await page.getByRole("button", { name: "Выполнено" }).click();
  await expect(page.getByText("Открытых задач нет")).toBeVisible();
  await page.getByRole("button", { name: "Выполненные" }).click();
  await expect(page.getByRole("heading", { name: "Ежедневная прогулка" })).toBeVisible();
});

test("shows Xiaomi-only coverage without page overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("./data-quality");
  await expect(page.getByRole("heading", { name: "Качество данных" })).toBeVisible();
  await expect(page.getByLabel("Состояние источников").getByText("Xiaomi Cloud")).toBeVisible();
  const steps = page.getByRole("row").filter({ hasText: "Шаги" });
  await expect(steps).toContainText("Только Xiaomi Cloud");
  await expect(page.getByText(/не подставляет шаги из Health Connect/i)).toBeVisible();
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
});

test("compares two laboratory panels in stable order", async ({ page }) => {
  await page.goto("./labs/compare");
  await page.getByLabel("Базовая панель").selectOption(labDocument.id);
  await page.getByLabel("Сравниваемая панель").selectOption(secondLabDocument.id);
  await page.getByRole("button", { name: "Сравнить", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Сопоставленные показатели" })).toBeVisible();
  const ferritin = page.getByRole("row").filter({ hasText: "Ферритин" });
  await expect(ferritin).toContainText("42,0 нг/мл");
  await expect(ferritin).toContainText("48,0 нг/мл");
  await expect(ferritin).toContainText("+6,0");
});

test("builds, downloads and explicitly deletes a doctor package on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("./reports/doctor");
  await expect(page.getByRole("heading", { name: "Пакет для врача" })).toBeVisible();
  await expect(page.getByLabel("AI-рекомендации")).not.toBeChecked();
  await page.getByRole("button", { name: "Сформировать preview и PDF" }).click();
  await expect(page.getByRole("heading", { name: "Preview пакета" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Шаги · Xiaomi Cloud" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Сон", exact: true })).toBeVisible();
  await expect(page.getByText("Ось Y — часы; подсказки — часы и минуты")).toBeVisible();
  const pdfLink = page.getByRole("link", { name: "Скачать PDF" });
  await pdfLink.evaluate((element) => element.removeAttribute("download"));
  const downloadPromise = page.waitForEvent("download");
  await pdfLink.click();
  expect((await downloadPromise).suggestedFilename()).toBe("amigo-doctor-report.pdf");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Удалить пакет" }).click();
  await expect(page.getByRole("heading", { name: "Preview пакета" })).not.toBeVisible();
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
});
