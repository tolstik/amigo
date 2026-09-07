import { useMemo } from "react";
import type { WeightRawPoint } from "../api/types";
import { dailyWeightChartOption } from "../charts/options";
import { dailyWeightCandles } from "../charts/weightCandles";
import { ChartCard } from "./ChartCard";
import { WeightCandlesTable } from "./DataTables";

export function WeightCandlestickChart({ points }: { points: WeightRawPoint[] }) {
  const candles = useMemo(() => dailyWeightCandles(points), [points]);
  const option = useMemo(() => dailyWeightChartOption(candles), [candles]);
  if (!candles.length) return null;
  return (
    <ChartCard
      title="Дневные изменения веса"
      subtitle="Все измерения Withings за последние 90 дней · дни по московскому времени"
      option={option}
      ariaLabel="Свечной график дневных изменений веса: первый и последний замеры, минимум и максимум в килограммах"
      height={360}
      footer={
        <>
          <ul className="weight-candle-legend" aria-label="Обозначения свечей веса">
            <li><span className="weight-candle-swatch weight-candle-swatch--down" />Снижение</li>
            <li><span className="weight-candle-swatch weight-candle-swatch--up" />Рост</li>
            <li><span className="weight-candle-swatch weight-candle-swatch--flat" />Без изменения / один замер</li>
          </ul>
          <p className="weight-candle-help">Тело свечи — от первого до последнего замера за день, тонкая линия — минимум и максимум. Один замер — черта, изменение неизвестно. Дни без замеров остаются пустыми. Ползунок под графиком меняет видимый период.</p>
          <WeightCandlesTable points={candles} />
        </>
      }
    />
  );
}
