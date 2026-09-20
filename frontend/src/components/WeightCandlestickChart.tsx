import { useMemo } from "react";
import type { WeightRawPoint } from "../api/types";
import { dailyWeightChartOption } from "../charts/options";
import { dailyWeightCandles, WEIGHT_CANDLE_DAYS } from "../charts/weightCandles";
import { ChartCard } from "./ChartCard";
import { WeightCandlesTable } from "./DataTables";
import { EmptyState } from "./AsyncState";
import { formatDate } from "../lib/format";

export function WeightCandlestickChart({ points, asOf, startDate, baselineKg }: {
  points: WeightRawPoint[];
  asOf: string;
  startDate?: string | null;
  baselineKg?: number | null;
}) {
  const options = useMemo(() => ({
    startAt: startDate ? `${startDate}T18:00:00+03:00` : undefined,
    baselineKg: baselineKg ?? undefined,
    baselineDate: startDate ?? undefined,
  }), [baselineKg, startDate]);
  const candles = useMemo(() => dailyWeightCandles(points, asOf, options), [points, asOf, options]);
  const option = useMemo(() => dailyWeightChartOption(candles, asOf), [candles, asOf]);
  if (!candles.length) return <EmptyState title={`За последние ${WEIGHT_CANDLE_DAYS} дней нет замеров веса`} text="Свечи появятся после следующего взвешивания и синхронизации Withings." />;
  return (
    <ChartCard
      title="Дневные изменения веса"
      subtitle={`${startDate ? `С вечера ${formatDate(startDate)} · ` : `Последние ${WEIGHT_CANDLE_DAYS} дней · `}изменение от предыдущего веса к текущему · Withings · МСК`}
      option={option}
      ariaLabel={`Свечной график дневных изменений веса за последние ${WEIGHT_CANDLE_DAYS} дней: от предыдущего веса к текущему в килограммах`}
      height={360}
      footer={
        <>
          <ul className="weight-candle-legend" aria-label="Обозначения свечей веса">
            <li><span className="weight-candle-swatch weight-candle-swatch--down" />Снижение</li>
            <li><span className="weight-candle-swatch weight-candle-swatch--up" />Рост</li>
            <li><span className="weight-candle-swatch weight-candle-swatch--flat" />Без изменения</li>
          </ul>
          <p className="weight-candle-help">Высота тела свечи — изменение от последнего веса предыдущего дня с замерами до последнего веса текущего дня. Тонкая линия учитывает минимум и максимум текущего дня. Дни без замеров пустые; после пропуска сравниваем с последним днём взвешивания.</p>
          <WeightCandlesTable points={candles} />
        </>
      }
    />
  );
}
