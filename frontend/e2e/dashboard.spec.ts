import { expect, test } from "@playwright/test";

const overview = {
  generated_at: "2026-08-19T08:00:00Z",
  plan: { start_date: "2026-08-15", start_weight_kg: 127.03, target_weight_kg: 76.5, target_date: "2027-09-04", planned_today_kg: 126.5 },
  weight: { latest_kg: 126.2, latest_at: "2026-08-19T05:00:00Z", smoothed_7d_kg: 126.4, change_since_start_kg: -0.63, deviation_from_plan_kg: -0.1, progress_pct: 1.2, trend_28d_kg: -0.6, measurement_days_30d: 4 },
  pressure: { latest_systolic: 122, latest_diastolic: 78, latest_pulse: 64, latest_at: "2026-08-18T18:00:00Z" },
  composition: { fat_pct: 31.2, fat_mass_kg: 39.4, lean_mass_kg: 86.8, measured_at: "2026-08-19T05:00:00Z" },
  sync: { status: "ok", last_success_at: "2026-08-19T08:00:00Z", source: "Withings Cloud" },
  insights: [{ id: "on-plan", title: "План соблюдается", text: "Сглаженный прогресс идёт рядом с плановой линией.", tone: "positive" }],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/overview")) return route.fulfill({ json: overview });
    if (path.endsWith("/insights")) return route.fulfill({ json: { items: overview.insights } });
    if (path.endsWith("/series/pressure")) {
      return route.fulfill({ json: { points: [{ measured_at: "2026-08-18T18:00:00Z", systolic: 122, diastolic: 78, pulse: 64, pulse_pressure: 44, session_size: 2, period_of_day: "evening" }], meta: { range: "90d", count: 1 } } });
    }
    return route.fulfill({ json: { points: [{ measured_at: "2026-08-19T05:00:00Z", weight_kg: 126.2, smoothed_7d_kg: 126.4, planned_kg: 126.5 }], meta: { range: "90d", count: 1 } } });
  });
});

test("renders the overview and navigates to pressure", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Добрый день! Вот как идут дела" })).toBeVisible();
  await expect(page.getByLabel("Главные показатели").getByText("126,2 кг")).toBeVisible();
  await page.getByRole("link", { name: "Давление", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Статистика давления" })).toBeVisible();
  await expect(page.getByText("122 / 78").first()).toBeVisible();
});
