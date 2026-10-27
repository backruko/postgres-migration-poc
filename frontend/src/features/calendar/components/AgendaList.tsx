import { PlusOutlined } from "@ant-design/icons";
import { Button } from "antd";
import type { CalendarDay, CalendarTemplateInfo, ClientOccurrence } from "../../../domain/plan/types";
import { weekdayJa } from "../../../shared/lib/datetime";
import { dayClass } from "./MonthGrid";
import { OccurrenceList, type OccurrenceActions } from "./OccurrenceList";

interface Props {
  days: CalendarDay[];
  visible: (occ: ClientOccurrence) => boolean;
  templates: Map<number, CalendarTemplateInfo>;
  timezone: string;
  now: number;
  today: string;
  actions: OccurrenceActions;
  onAddOneOff: (date: string) => void;
}

/** 狭い画面用: 予定のある日（と休日）だけを日付順に並べたリスト */
export function AgendaList({ days, visible, templates, timezone, now, today, actions, onAddOneOff }: Props) {
  const rows = days
    .map((day) => ({ day, items: day.occurrences.filter(visible) }))
    .filter(({ day, items }) => items.length || day.holiday_name);

  return (
    <section className="agenda" aria-label="予定リスト">
      <div className="agenda-day">
        <Button icon={<PlusOutlined />} onClick={() => onAddOneOff(days[0]?.date ?? "")} disabled={!templates.size}>
          臨時の予定を追加
        </Button>
      </div>
      {rows.length === 0 && <p className="empty agenda-day">表示する予定がありません。</p>}
      {rows.map(({ day, items }) => {
        const [, m, d] = day.date.split("-").map(Number);
        return (
          <div key={day.date} className={`agenda-day ${dayClass(day)}${day.date < today ? " past" : ""}`}>
            <div className="agenda-head">
              <span className="day-num">{m}/{d}</span>
              <span className="agenda-wd">
                {weekdayJa(day.weekday)}
                {day.holiday_name ? ` ・ ${day.holiday_name}` : ""}
              </span>
            </div>
            {items.length ? (
              <OccurrenceList occurrences={items} templates={templates} timezone={timezone} now={now} actions={actions} />
            ) : (
              <p className="empty">予定なし</p>
            )}
          </div>
        );
      })}
    </section>
  );
}
