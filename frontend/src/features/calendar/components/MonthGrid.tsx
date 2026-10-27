import type { CalendarDay, ClientOccurrence } from "../../../domain/plan/types";
import { weekdayJa } from "../../../shared/lib/datetime";
import { OccurrenceChip } from "./OccurrenceChip";

const MAX_CHIPS = 4;
const HEADER = [7, 1, 2, 3, 4, 5, 6]; // 日曜始まり（ISO 曜日）

interface Props {
  days: CalendarDay[];
  visible: (occ: ClientOccurrence) => boolean;
  selected: string | null;
  today: string;
  /** 現在時刻（ミリ秒）。終了した予定を薄く表示するために使う */
  now: number;
  timezone: string;
  onSelect: (date: string) => void;
}

export function dayClass(day: CalendarDay): string {
  if (day.holiday_name || day.weekday === 7) return "is-sun";
  if (day.weekday === 6) return "is-sat";
  return "";
}

/** 日曜始まりの月グリッド */
export function MonthGrid({ days, visible, selected, today, now, timezone, onSelect }: Props) {
  if (!days.length) return null;
  const leading = days[0].weekday % 7; // 日曜=0
  const trailing = (7 - ((leading + days.length) % 7)) % 7;

  return (
    <section className="month" aria-label="月カレンダー">
      <div className="dow" aria-hidden="true">
        {HEADER.map((wd) => (
          <div key={wd} className={wd === 7 ? "is-sun" : wd === 6 ? "is-sat" : ""}>
            {weekdayJa(wd)}
          </div>
        ))}
      </div>
      <div className="grid">
        {Array.from({ length: leading }, (_, i) => (
          <div key={`lead-${i}`} className="day blank" aria-hidden="true" />
        ))}
        {days.map((day) => {
          const items = day.occurrences.filter(visible);
          const classes = [
            "day",
            dayClass(day),
            day.is_business_day === false && "non-business",
            day.date === today && "today",
            day.date < today && "past",
          ].filter(Boolean).join(" ");
          return (
            <button
              type="button"
              key={day.date}
              className={classes}
              aria-pressed={day.date === selected}
              aria-label={`${day.date} ${weekdayJa(day.weekday)}曜日 ${day.holiday_name ?? ""} 予定${items.length}件`}
              onClick={() => onSelect(day.date)}
            >
              <span className="day-head">
                <span className="day-num">{Number(day.date.slice(8))}</span>
                {day.holiday_name && <span className="day-holiday">{day.holiday_name}</span>}
              </span>
              {items.slice(0, MAX_CHIPS).map((occ) => (
                <OccurrenceChip key={occ.instance_id} occ={occ} timezone={timezone} now={now} />
              ))}
              {items.length > MAX_CHIPS && <span className="more">ほか {items.length - MAX_CHIPS} 件</span>}
            </button>
          );
        })}
        {Array.from({ length: trailing }, (_, i) => (
          <div key={`trail-${i}`} className="day blank" aria-hidden="true" />
        ))}
      </div>
    </section>
  );
}
