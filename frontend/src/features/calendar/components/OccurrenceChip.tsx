import type { ClientOccurrence } from "../../../domain/plan/types";
import { timeIn } from "../../../shared/lib/datetime";
import { chipStyle } from "../lib/chipStyle";
import { isPastOccurrence } from "../lib/past";
import { Icon } from "./Icon";

/** 月グリッドのマス内に並べる1件分の予定 */
export function OccurrenceChip({ occ, timezone, now }: { occ: ClientOccurrence; timezone: string; now: number }) {
  const s = chipStyle(occ.ui_metadata);
  const start = timeIn(occ.starts_at, timezone);
  const end = timeIn(occ.ends_at, timezone);
  const past = isPastOccurrence(occ, now);
  const classes = ["chip", s.bold && "bold", occ.origin !== "generated" && "manual", past && "past"]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      style={{ background: s.background, color: s.color }}
      title={`${start}–${end} ${occ.title}（${occ.biz_code} ${occ.biz_name}）${past ? " ・ 終了" : ""}`}
    >
      <Icon name={s.icon} />
      <span className="chip-time">{start}</span>
      <span className="chip-title">{occ.title}</span>
    </div>
  );
}
