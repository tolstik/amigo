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
  meta: { range: "90d", count: 2 },
};

const recoverySeries = {
  data_as_of: "2026-09-02T08:00:00Z",
  daily: [
    { date: "2026-09-01", sleep_minutes: 438, deep_sleep_minutes: 91, rem_sleep_minutes: 102, resting_heart_rate_bpm: 62, hrv_rmssd_ms: 43, spo2_pct: 97.1 },
    { date: "2026-09-02", sleep_minutes: 461, deep_sleep_minutes: 98, rem_sleep_minutes: 110, resting_heart_rate_bpm: 60, hrv_rmssd_ms: 46, spo2_pct: 97.4 },
  ],
  summary: { sleep_minutes: 461, baseline_sleep_minutes: 445, resting_heart_rate_bpm: 60, baseline_resting_heart_rate_bpm: 62, hrv_rmssd_ms: 46, baseline_hrv_rmssd_ms: 42, spo2_pct: 97.4, data_as_of: "2026-09-02T08:00:00Z" },
  available_metrics: ["sleep", "resting_heart_rate", "hrv", "spo2"],
  meta: { range: "90d", count: 2 },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/overview")) return route.fulfill({ json: overview });
    if (path.endsWith("/ai-analysis")) return route.fulfill({ json: {
      status: "fresh",
      headline: "Динамика остаётся управляемой",
      summary: "Активность выше личной базы, а последние ночи сна стали немного длиннее.",
      insights: [{ id: "i1", title: "Больше движения", text: "Недельная активность выше вашей личной базы.", evidence_ids: ["activity.steps.week"] }],
      recommendations: [{ id: "r1", title: "Сохранить ритм", text: "Поддерживайте текущую регулярность прогулок.", evidence_ids: ["activity.steps.week"] }],
      limitations: ["Текущая неделя ещё не завершена"],
      generated_at: "2026-09-02T07:55:00Z",
      data_as_of: "2026-09-02T07:50:00Z",
      model: "gpt-5.6-sol",
    } });
    if (path.endsWith("/insights")) return route.fulfill({ json: { items: overview.insights } });
    if (path.endsWith("/series/pressure")) {
      return route.fulfill({ json: { points: [{ measured_at: "2026-08-18T18:00:00Z", systolic: 122, diastolic: 78, pulse: 64, pulse_pressure: 44, session_size: 2, period_of_day: "evening" }], meta: { range: "90d", count: 1 } } });
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

  await page.getByRole("link", { name: "Восстановление", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Восстановление", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Сон", exact: true })).toBeVisible();
  await expect(
    page.getByRole("article").filter({ hasText: "Последний сон" }).getByText("7 ч 41 мин", { exact: true }),
  ).toBeVisible();
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
