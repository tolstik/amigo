import type { WeightPoint } from "../api/types";
import { formatDate, formatDelta, shiftIsoDate } from "../lib/format";

export type CalendarCellState = "ahead" | "near" | "behind" | "measured" | "outlier" | "empty" | "future";

export interface CalendarCell {
  day: string;
  state: CalendarCellState;
  deviationKg: number | null;
}

/** ±0.3 kg around plan counts as "near plan"; colours never encode a diagnosis. */
export const NEAR_PLAN_KG = 0.3;

function weekday(day: string): number {
  // Monday = 0 … Sunday = 6 for a Moscow calendar date.
  return (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
}

export function buildCalendar(points: readonly WeightPoint[], startDate: string, today: string): CalendarCell[][] {
  const byDay = new Map(points.map((point) => [point.measuredAt.slice(0, 10), point]));
  const first = shiftIsoDate(startDate, -weekday(startDate));
  const last = shiftIsoDate(today, 6 - weekday(today));
  const weeks: CalendarCell[][] = [];
  for (let day = first; day <= last; day = shiftIsoDate(day, 1)) {
    if (weekday(day) === 0) weeks.push([]);
    const point = byDay.get(day);
    let state: CalendarCellState = "empty";
    let deviationKg: number | null = null;
    if (day > today) state = "future";
    else if (day < startDate) state = "future";
    else if (point?.isOutlier) state = "outlier";
    else if (point) {
      if (point.plannedKg === null) state = "measured";
      else {
        deviationKg = point.weightKg - point.plannedKg;
        state = Math.abs(deviationKg) <= NEAR_PLAN_KG ? "near" : deviationKg < 0 ? "ahead" : "behind";
      }
    }
    weeks[weeks.length - 1].push({ day, state, deviationKg });
  }
  return weeks;
}

const stateLabels: Record<CalendarCellState, string> = {
  ahead: "ниже плана",
  near: "рядом с планом",
  behind: "выше плана",
  measured: "замер",
  outlier: "необычный замер",
  empty: "нет замера",
  future: "",
};

const weekdayLabels = ["Пн", "", "Ср", "", "Пт", "", "Вс"];

export function RegularityCalendar({ points, startDate, today }: { points: readonly WeightPoint[]; startDate: string; today: string }) {
  if (!startDate) return null;
  const weeks = buildCalendar(points, startDate, today);
  const measured = weeks.flat().filter((cell) => !["empty", "future"].includes(cell.state)).length;
  const elapsed = weeks.flat().filter((cell) => cell.state !== "future").length;
  return (
    <section className="panel calendar-panel" aria-labelledby="regularity-calendar-title">
      <div className="panel__head">
        <div>
          <span className="eyebrow">Регулярность взвешиваний</span>
          <h2 id="regularity-calendar-title">Календарь программы</h2>
          <p className="chart-note">Каждая клетка — день по Москве. Цвет сравнивает дневную медиану с планом на ту же дату (±{NEAR_PLAN_KG.toLocaleString("ru-RU")} кг — рядом с планом). Пустая клетка — замера не было.</p>
        </div>
        <span className="calendar-panel__count"><strong>{measured}</strong> из {elapsed} дней</span>
      </div>
      <div className="calendar-scroll">
        <div className="calendar-grid" role="grid" aria-label="Календарь замеров веса относительно плана">
          <div className="calendar-weekdays" aria-hidden="true">
            {weekdayLabels.map((label, index) => <span key={index}>{label}</span>)}
          </div>
          {weeks.map((week) => (
            <div className="calendar-week" role="row" key={week[0].day}>
              {week.map((cell) => {
                const label = cell.state === "future"
                  ? ""
                  : `${formatDate(cell.day)}: ${stateLabels[cell.state]}${cell.deviationKg !== null ? ` (${formatDelta(cell.deviationKg, "кг", 2)} к плану)` : ""}`;
                return (
                  <span
                    key={cell.day}
                    role="gridcell"
                    className={`calendar-cell calendar-cell--${cell.state}`}
                    title={label || undefined}
                    aria-label={label || undefined}
                    aria-hidden={cell.state === "future" ? true : undefined}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <ul className="calendar-legend" aria-label="Обозначения календаря">
        <li><span className="calendar-cell calendar-cell--ahead" />Ниже плана</li>
        <li><span className="calendar-cell calendar-cell--near" />Рядом с планом</li>
        <li><span className="calendar-cell calendar-cell--behind" />Выше плана</li>
        <li><span className="calendar-cell calendar-cell--outlier" />Необычный замер</li>
        <li><span className="calendar-cell calendar-cell--empty" />Нет замера</li>
      </ul>
    </section>
  );
}
