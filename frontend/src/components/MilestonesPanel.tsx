import type { MilestonesResponse } from "../api/types";
import { formatDate, formatDelta, formatKg, formatNumber } from "../lib/format";
import { Icon } from "./Icon";

function days(value: number): string {
  const mod10 = value % 10;
  const mod100 = value % 100;
  if (mod10 === 1 && mod100 !== 11) return `${value} день`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${value} дня`;
  return `${value} дней`;
}

export function MilestonesPanel({ data }: { data: MilestonesResponse }) {
  const achieved = data.items.filter((item) => item.achievedOn);
  return (
    <section className="panel milestones-panel" aria-labelledby="milestones-title">
      <div className="panel__head">
        <div>
          <span className="eyebrow">Детерминированные факты</span>
          <h2 id="milestones-title">Вехи программы</h2>
        </div>
        <span className="milestones-panel__count"><strong>{achieved.length}</strong> из {data.items.length}</span>
      </div>

      <div className="milestone-stats">
        <div>
          <span className="milestone-stats__icon milestone-stats__icon--coral"><Icon name="flame" /></span>
          <span><small>Серия взвешиваний</small><strong>{days(data.streaks.currentDays)}</strong><em>Рекорд — {days(data.streaks.longestDays)}{data.streaks.longestEnd ? ` · до ${formatDate(data.streaks.longestEnd)}` : ""}</em></span>
        </div>
        <div>
          <span className="milestone-stats__icon milestone-stats__icon--green"><Icon name="trophy" /></span>
          <span><small>Лучшая неделя</small><strong>{data.bestWeek ? formatDelta(data.bestWeek.changeKg, "кг", 2) : "—"}</strong><em>{data.bestWeek ? `${formatDate(data.bestWeek.startDate, false)} – ${formatDate(data.bestWeek.endDate)}` : "Нужна завершённая неделя со снижением"}</em></span>
        </div>
        <div>
          <span className="milestone-stats__icon milestone-stats__icon--blue"><Icon name="calendar" /></span>
          <span><small>Лучший месяц</small><strong>{data.bestMonth ? formatDelta(data.bestMonth.changeKg, "кг", 2) : "—"}</strong><em>{data.bestMonth ? `${formatDate(data.bestMonth.startDate, false)} – ${formatDate(data.bestMonth.endDate)}` : "Нужен завершённый месяц со снижением"}</em></span>
        </div>
        <div>
          <span className={`milestone-stats__icon ${data.plateau.active ? "milestone-stats__icon--amber" : "milestone-stats__icon--violet"}`}><Icon name="pause" /></span>
          <span>
            <small>Плато за {data.plateau.windowDays} дней</small>
            <strong>{data.plateau.active ? "Тренд не снижается" : data.plateau.changeKg !== null ? "Нет" : "—"}</strong>
            <em>{data.plateau.changeKg !== null
              ? `Тренд ${formatDelta(data.plateau.changeKg, "кг", 2)} · порог −${formatNumber(data.plateau.thresholdKg)} кг`
              : data.plateau.reason === "stale" ? "Нужен свежий замер" : "Недостаточно замеров"}</em>
          </span>
        </div>
      </div>

      {data.next && (
        <p className="milestone-next">
          Следующая веха — <strong>{data.next.label}</strong> ({formatKg(data.next.thresholdKg, 1)})
          {data.next.remainingKg !== null && data.next.remainingKg > 0 ? <>: осталось <strong>{formatKg(data.next.remainingKg, 1)}</strong></> : null}
        </p>
      )}

      <ol className="milestone-list">
        {data.items.map((item) => (
          <li key={item.id} className={item.achievedOn ? "is-achieved" : undefined}>
            <span className="milestone-list__mark" aria-hidden="true">{item.achievedOn ? "✓" : ""}</span>
            <span className="milestone-list__label">{item.label}<small>{formatKg(item.thresholdKg, 1)}</small></span>
            <span className="milestone-list__date">{item.achievedOn ? formatDate(item.achievedOn) : "впереди"}</span>
          </li>
        ))}
      </ol>
      <p className="chart-note">Вехи считаются по дневным медианам без необычных замеров, серия — по дням с любым взвешиванием. Пороги ИМТ — справочные значения по росту из профиля, а не оценка здоровья.</p>
    </section>
  );
}
