import { useMemo, useState } from "react";
import type { HeartRateHourlyPoint } from "../api/types";
import {
  aggregateHeartRate,
  defaultHeartRateAggregation,
  HEART_RATE_AGGREGATION_OPTIONS,
  type HeartRateAggregationHours,
} from "../charts/heartRate";
import { heartRateChartOption } from "../charts/options";
import { ChartCard } from "./ChartCard";

interface WatchHeartRateChartProps {
  points: HeartRateHourlyPoint[];
}

export function WatchHeartRateChart({ points }: WatchHeartRateChartProps) {
  const [manualAggregationHours, setManualAggregationHours] = useState<HeartRateAggregationHours | null>(null);
  const automaticAggregationHours = useMemo(() => defaultHeartRateAggregation(points), [points]);
  const aggregationHours = manualAggregationHours ?? automaticAggregationHours;
  const aggregated = useMemo(() => aggregateHeartRate(points, aggregationHours), [aggregationHours, points]);
  const selected = HEART_RATE_AGGREGATION_OPTIONS.find((option) => option.hours === aggregationHours)!;
  const option = useMemo(
    () => heartRateChartOption(aggregated, aggregationHours),
    [aggregated, aggregationHours],
  );

  return (
    <ChartCard
      title="Пульс с часов"
      subtitle={`Интервал: ${selected.description}. Минимум и максимум сохраняют границы интервала, среднее учитывает число замеров; исходные замеры не сохраняются.`}
      option={option}
      ariaLabel={`График среднего, минимального и максимального пульса с часов, интервал ${selected.description}`}
      height={430}
      aside={(
        <fieldset className="heart-rate-aggregation">
          <legend>Группировка</legend>
          <div className="segmented">
            {HEART_RATE_AGGREGATION_OPTIONS.map((item) => (
              <button
                key={item.hours}
                type="button"
                className={item.hours === aggregationHours ? "is-active" : ""}
                aria-pressed={item.hours === aggregationHours}
                onClick={() => setManualAggregationHours(item.hours)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      footer={(
        <p className="heart-rate-chart-help">
          Масштабируйте колесом мыши или жестом, а видимое окно меняйте нижним ползунком. Пропуски данных показаны разрывами.
        </p>
      )}
    />
  );
}
