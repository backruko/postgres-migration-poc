// テンプレートの「保存の前の確認」の結果（POST /ops/templates/…/preview）を、画面の表示用にまとめる。UI から独立した純粋な処理。
import type { OrderStatus } from "../../domain/ope/types";
import type { PreviewOccurrence, TemplateChangePreview, TemplateCreatePreview } from "../../domain/plan/types";
import { addMonths, inZone, monthOf } from "../../shared/lib/datetime";

/** 確認できる範囲（か月）。既定は 3 か月 */
export const PREVIEW_MONTH_OPTIONS = [3, 6, 12] as const;
export const DEFAULT_PREVIEW_MONTHS = 3;

/**
 * 1 日の中の予定 1 件の印。
 * same: 変わらない / added: 増える / removed: 無くなる / moved: 日時が変わる（変更の後）/ moved_from: 日時が変わる前（別の日に動く場合だけ）
 * manual: 人が変更・追加した予定（テンプレートを変えても動かない）
 */
export type MarkKind = "same" | "added" | "removed" | "moved" | "moved_from" | "manual";

export interface DayMark {
  kind: MarkKind;
  /** その日の時刻（HH:mm）。moved は変更の後、moved_from・removed は変更の前 */
  time: string;
  /** moved: 変更の前（"M/D HH:mm"。同じ日なら "HH:mm"）/ moved_from: 変更の後 */
  other?: string;
}

export const MARK_LABEL: Record<MarkKind, string> = {
  same: "変わらない",
  added: "増える",
  removed: "無くなる",
  moved: "日時が変わる",
  moved_from: "別の日に動く",
  manual: "人が変更した予定（そのまま）",
};

const day = (iso: string, tz: string) => inZone(iso, tz).format("YYYY-MM-DD");
const hm = (iso: string, tz: string) => inZone(iso, tz).format("HH:mm");

function push(map: Map<string, DayMark[]>, date: string, mark: DayMark) {
  const list = map.get(date);
  if (list) list.push(mark);
  else map.set(date, [mark]);
}

/** 新しいテンプレートの確認: できる予定をそのまま日ごとに並べる */
export function marksOfCreate(p: TemplateCreatePreview, tz: string): Map<string, DayMark[]> {
  const map = new Map<string, DayMark[]>();
  for (const o of p.occurrences) push(map, day(o.scheduled_start, tz), { kind: "added", time: hm(o.scheduled_start, tz) });
  return map;
}

/**
 * 変更の確認: 変更の後の予定に「変わらない・増える・日時が変わる・人が変更した」の印を付け、
 * 無くなる予定と、別の日へ動く予定の元の日も載せる
 */
export function marksOfChange(p: TemplateChangePreview, tz: string): Map<string, DayMark[]> {
  const map = new Map<string, DayMark[]>();
  const key = (o: PreviewOccurrence) => `${o.occurrence_date}|${o.scheduled_start}`;
  const added = new Set(p.added.map(key));
  const movedByAfter = new Map(p.moved.map((m) => [`${m.occurrence_date}|${m.after_start}`, m]));

  for (const o of p.occurrences) {
    const date = day(o.scheduled_start, tz);
    const time = hm(o.scheduled_start, tz);
    const move = movedByAfter.get(key(o));
    if (o.manual) {
      push(map, date, { kind: "manual", time });
    } else if (move) {
      const beforeDay = day(move.before_start, tz);
      const beforeTime = hm(move.before_start, tz);
      const sameDay = beforeDay === date;
      push(map, date, { kind: "moved", time, other: sameDay ? beforeTime : `${shortDay(beforeDay)} ${beforeTime}` });
      if (!sameDay) push(map, beforeDay, { kind: "moved_from", time: beforeTime, other: `${shortDay(date)} ${time}` });
    } else if (added.has(key(o))) {
      push(map, date, { kind: "added", time });
    } else {
      push(map, date, { kind: "same", time });
    }
  }
  for (const o of p.removed) push(map, day(o.scheduled_start, tz), { kind: "removed", time: hm(o.scheduled_start, tz) });
  for (const list of map.values()) list.sort((a, b) => a.time.localeCompare(b.time));
  return map;
}

const shortDay = (date: string) => `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;

/** 表示する月（"YYYY-MM"）: 範囲の始まりの月から、範囲の終わり・印のある最後の日のどちらか遅い月まで */
export function monthsToShow(rangeStart: string, rangeEnd: string, marks: Map<string, DayMark[]>, tz: string): string[] {
  const first = monthOf(day(rangeStart, tz));
  // 範囲の終わりは「次の日の 0:00」なので、1 秒前の日を最後の日にする
  let last = monthOf(inZone(rangeEnd, tz).subtract(1, "second").format("YYYY-MM-DD"));
  for (const date of marks.keys()) {
    if (monthOf(date) > last) last = monthOf(date);
  }
  const months: string[] = [];
  for (let m = first; m <= last && months.length < 36; m = addMonths(m, 1)) months.push(m);
  return months;
}

export interface MonthCell {
  /** YYYY-MM-DD。月の前後の空きは null */
  date: string | null;
  dayOfMonth: number;
  marks: DayMark[];
}

/** 月の升目（日曜始まり、7 列）。前後の空きは date: null */
export function monthCells(month: string, marks: Map<string, DayMark[]>): MonthCell[] {
  const [y, m] = month.split("-").map(Number);
  const firstWeekday = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(); // 0 = 日
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const cells: MonthCell[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push({ date: null, dayOfMonth: 0, marks: [] });
  for (let d = 1; d <= days; d++) {
    const date = `${month}-${String(d).padStart(2, "0")}`;
    cells.push({ date, dayOfMonth: d, marks: marks.get(date) ?? [] });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, dayOfMonth: 0, marks: [] });
  return cells;
}

/** 升目の色を決める印（1 日に複数あるときは、気を付けるものを優先） */
const PRIORITY: MarkKind[] = ["removed", "added", "moved", "moved_from", "manual", "same"];
export const mainKind = (marks: DayMark[]): MarkKind | null => PRIORITY.find((k) => marks.some((m) => m.kind === k)) ?? null;

/** 升目の説明（マウスを置いたとき・読み上げ用） */
export function describeMarks(marks: DayMark[]): string {
  return marks
    .map((m) => {
      switch (m.kind) {
        case "moved":
          return `${m.time} 日時が変わる（元: ${m.other}）`;
        case "moved_from":
          return `${m.time} 別の日に動く（→ ${m.other}）`;
        default:
          return `${m.time} ${MARK_LABEL[m.kind]}`;
      }
    })
    .join("\n");
}

// ---------------- オーダーへの影響 ----------------

/** リリースより後の状態（現場がもう見ている・動いているオーダー） */
const RELEASED: OrderStatus[] = ["ready", "in_progress", "on_hold"];

export interface OrderImpact {
  rescheduled: number;
  /** rescheduled のうち、リリース済み・保留中のもの */
  rescheduledReleased: number;
  canceled: number;
  canceledReleased: number;
  /** 矛盾（作業中などで自動では動かせず、人の判断を待つ） */
  conflict: number;
  created: number;
  total: number;
  /** 何かが起きるか */
  any: boolean;
}

export function orderImpact(p: TemplateChangePreview): OrderImpact {
  const of = (effect: string) => p.order_effects.filter((e) => e.effect === effect);
  const released = (effect: string) => of(effect).filter((e) => RELEASED.includes(e.status)).length;
  const impact = {
    rescheduled: of("rescheduled").length,
    rescheduledReleased: released("rescheduled"),
    canceled: of("canceled").length,
    canceledReleased: released("canceled"),
    conflict: of("conflict").length,
    created: p.orders_created,
    total: p.orders_total,
  };
  return { ...impact, any: impact.rescheduled + impact.canceled + impact.conflict + impact.created > 0 };
}

/** 変更の確認で、予定に違いがあるか（無ければ「日付は変わりません」と出す） */
export const hasDateChanges = (p: TemplateChangePreview) => p.added.length + p.removed.length + p.moved.length > 0;
