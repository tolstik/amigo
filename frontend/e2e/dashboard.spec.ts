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

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/overview")) return route.fulfill({ json: overview });
    if (path.endsWith("/insights")) return route.fulfill({ json: { items: overview.insights } });
    if (path.endsWith("/series/pressure")) {
      return route.fulfill({ json: { points: [{ measured_at: "2026-08-18T18:00:00Z", systolic: 122, diastolic: 78, pulse: 64, pulse_pressure: 44, session_size: 2, period_of_day: "evening" }], meta: { range: "90d", count: 1 } } });
    }
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
