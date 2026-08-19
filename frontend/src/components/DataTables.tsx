import type { CompositionPoint, PressurePoint, WeightPoint } from "../api/types";
import { formatDate, formatDateTime, formatKg, formatNumber } from "../lib/format";

function TableFrame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="data-table-wrap">
      <summary>{title}</summary>
      <div className="data-table-scroll">{children}</div>
    </details>
  );
}

export function WeightTable({ points }: { points: WeightPoint[] }) {
  const rows = points.slice(-50).reverse();
  return (
    <TableFrame title={`Показать дневные медианы (${Math.min(points.length, 50)} последних)`}>
      <table className="data-table">
        <caption className="sr-only">Последние дневные медианы веса</caption>
        <thead><tr><th>Дата</th><th>Вес</th><th>Тренд 7 дней</th><th>План</th><th>Примечание</th></tr></thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.measuredAt}>
              <td>{formatDate(point.measuredAt)}</td>
              <td>{formatKg(point.weightKg)}</td>
              <td>{formatKg(point.smoothed7dKg)}</td>
              <td>{formatKg(point.plannedKg)}</td>
              <td>{point.isOutlier ? "Не участвует в тренде" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableFrame>
  );
}

export function PressureTable({ points }: { points: PressurePoint[] }) {
  const rows = points.slice(-50).reverse();
  return (
    <TableFrame title={`Показать таблицу сессий (${Math.min(points.length, 50)} последних)`}>
      <table className="data-table">
        <caption className="sr-only">Последние измерения давления</caption>
        <thead><tr><th>Дата</th><th>Систолическое</th><th>Диастолическое</th><th>Пульс</th><th>Пульсовое</th><th>Замеров</th></tr></thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.measuredAt}>
              <td>{formatDateTime(point.measuredAt)}</td>
              <td>{formatNumber(point.systolic, 0)}</td>
              <td>{formatNumber(point.diastolic, 0)}</td>
              <td>{formatNumber(point.pulse, 0)}</td>
              <td>{formatNumber(point.pulsePressure, 0)}</td>
              <td>{point.sessionSize}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableFrame>
  );
}

export function CompositionTable({ points }: { points: CompositionPoint[] }) {
  const rows = points.slice(-50).reverse();
  return (
    <TableFrame title={`Показать таблицу замеров (${Math.min(points.length, 50)} последних)`}>
      <table className="data-table">
        <caption className="sr-only">Последние оценки состава тела</caption>
        <thead><tr><th>Дата</th><th>Доля жира</th><th>Жировая масса</th><th>Безжировая масса</th></tr></thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.measuredAt}>
              <td>{formatDateTime(point.measuredAt)}</td>
              <td>{point.fatPct === null ? "—" : `${formatNumber(point.fatPct)}%`}</td>
              <td>{formatKg(point.fatMassKg)}</td>
              <td>{formatKg(point.leanMassKg)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableFrame>
  );
}
