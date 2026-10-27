import { PlusOutlined } from "@ant-design/icons";
import { Button } from "antd";
import type { CalendarDay, CalendarTemplateInfo, ClientOccurrence } from "../../../domain/plan/types";
import { weekdayJa } from "../../../shared/lib/datetime";
import { OccurrenceList, type OccurrenceActions } from "./OccurrenceList";

interface Props {
  day: CalendarDay | undefined;
  visible: (occ: ClientOccurrence) => boolean;
  templates: Map<number, CalendarTemplateInfo>;
  timezone: string;
  now: number;
  today: string;
  actions: OccurrenceActions;
  onAddOneOff: (date: string) => void;
}

/** 選択した日の明細（広い画面で右側に表示） */
export function DayDetail({ day, visible, templates, timezone, now, today, actions, onAddOneOff }: Props) {
  if (!day) return <aside className="detail" />;
  const items = day.occurrences.filter(visible);
  const [, m, d] = day.date.split("-").map(Number);

  return (
    <aside className={day.date < today ? "detail past-day" : "detail"} aria-live="polite">
      <div className="detail-head">
        <h2>
          {m}月{d}日<span className="detail-wd">{weekdayJa(day.weekday)}曜日</span>
        </h2>
        <Button size="small" icon={<PlusOutlined />} onClick={() => onAddOneOff(day.date)} disabled={!templates.size}>
          臨時の予定
        </Button>
      </div>
      {day.holiday_name && <div className="detail-holiday">休日 ・ {day.holiday_name}</div>}
      <div className="detail-count">予定 {items.length} 件</div>
      {items.length ? (
        <OccurrenceList occurrences={items} templates={templates} timezone={timezone} now={now} actions={actions} />
      ) : (
        <p className="empty">この日の予定はありません。</p>
      )}
    </aside>
  );
}
