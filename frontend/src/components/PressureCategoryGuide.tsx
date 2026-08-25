import type { DailyPressureCategory, PressureCategory } from "../lib/pressureCategories";
import {
  PRESSURE_CATEGORY_DEFINITIONS,
  PRESSURE_CATEGORY_LEGEND_ORDER,
} from "../lib/pressureCategories";
import { formatDate, formatNumber } from "../lib/format";

const CATEGORY_CLASS: Record<PressureCategory, string> = {
  below_guide: "pressure-category-swatch--below",
  home_guide: "pressure-category-swatch--home",
  elevated: "pressure-category-swatch--elevated",
  critical_high: "pressure-category-swatch--critical",
};

function range(minimum: number, maximum: number): string {
  if (minimum === maximum) return formatNumber(minimum, 0);
  return `${formatNumber(minimum, 0)}–${formatNumber(maximum, 0)}`;
}

export function PressureCategoryGuide({ days }: { days: DailyPressureCategory[] }) {
  const hasCriticalHigh = days.some((day) => day.category === "critical_high");
  return (
    <div className="pressure-category-guide">
      <ul className="pressure-category-legend" aria-label="Границы категорий давления">
        {PRESSURE_CATEGORY_LEGEND_ORDER.map((category) => {
          const definition = PRESSURE_CATEGORY_DEFINITIONS[category];
          return (
            <li key={category}>
              <span
                className={`pressure-category-swatch ${CATEGORY_CLASS[category]}`}
                aria-hidden="true"
              />
              <span><strong>{definition.label}</strong><small>{definition.boundary}</small></span>
            </li>
          );
        })}
      </ul>
      <p className="pressure-category-disclaimer">
        <strong>Это визуальный ориентир, а не диагноз.</strong>{" "}
        Он помогает разделить дни по заданным порогам домашних измерений и не заменяет медицинскую оценку.
      </p>
      {hasCriticalHigh && <p className="pressure-category-disclaimer pressure-category-disclaimer--critical">
        <strong>В истории есть значение в красной категории.</strong>{" "}
        При систолическом значении ≥ 180 или диастолическом ≥ 120 повторите измерение.
        При симптомах или резком ухудшении самочувствия обращайтесь за экстренной медицинской помощью.
      </p>}
      <details className="data-table-wrap">
        <summary>Показать дневные категории ({days.length})</summary>
        <div className="data-table-scroll">
          <table className="data-table">
            <caption className="sr-only">Дневные категории давления и диапазоны сессий</caption>
            <thead><tr><th scope="col">Дата</th><th scope="col">Категория дня</th><th scope="col">Диапазон, сист. / диаст.</th><th scope="col">Сессий</th></tr></thead>
            <tbody>
              {[...days].reverse().map((day) => (
                <tr key={day.date}>
                  <th scope="row">{formatDate(day.date)}</th>
                  <td>{PRESSURE_CATEGORY_DEFINITIONS[day.category].label}</td>
                  <td>{range(day.minSystolic, day.maxSystolic)} / {range(day.minDiastolic, day.maxDiastolic)}</td>
                  <td>{day.sessions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
