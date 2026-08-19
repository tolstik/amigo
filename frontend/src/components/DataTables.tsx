import type { CompositionPoint, PressurePoint, WeeklyWeightPoint, WeightPoint } from "../api/types";
import { formatDate, formatDateTime, formatDelta, formatKg, formatNumber } from "../lib/format";

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

function weeklyNote(point: WeeklyWeightPoint): string {
  const notes: string[] = [];
  if (point.measurementDays === 0) notes.push("Нет замеров");
  if (point.isPartial) notes.push("Неполная неделя");
  if (point.outlierDays > 0) notes.push(`Дней-выбросов: ${point.outlierDays}`);
  return notes.join(" · ") || "—";
}

export function WeeklyWeightTable({ points }: { points: WeeklyWeightPoint[] }) {
  const rows = [...points].reverse();
  return (
    <TableFrame title={`Показать недельную таблицу (${points.length})`}>
      <table className="data-table">
        <caption className="sr-only">Недельные показатели веса относительно плана</caption>
        <thead>
          <tr>
            <th scope="col">Неделя</th>
            <th scope="col">Факт, средний</th>
            <th scope="col">План, средний</th>
            <th scope="col">Минимум</th>
            <th scope="col">Изменение, факт</th>
            <th scope="col">Изменение, план</th>
            <th scope="col">Факт − план</th>
            <th scope="col">Дни / замеры</th>
            <th scope="col">Примечание</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.startDate}>
              <th scope="row">{formatDate(point.startDate)} — {formatDate(point.endDate)}</th>
              <td>{formatKg(point.actualAvgKg)}</td>
              <td>{formatKg(point.plannedAvgKg)}</td>
              <td>{formatKg(point.actualMinKg)}</td>
              <td>{formatDelta(point.actualChangeKg)}</td>
              <td>{formatDelta(point.plannedChangeKg)}</td>
              <td>{formatDelta(point.deviationFromPlanKg)}</td>
              <td>{point.measurementDays} / {point.sampleCount}</td>
              <td>{weeklyNote(point)}</td>
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
