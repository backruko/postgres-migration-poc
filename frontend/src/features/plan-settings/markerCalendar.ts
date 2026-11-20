// マーカーの日付を年のカレンダーで足す・消す。UI から独立した純粋な処理。
// 日をクリックすると、保存前の変更（足す / 消す）に入る・外れる。保存は 1 回（まとめての API）。最終的な判定はバックエンド。
import type { ReferenceSheet, ReferenceSheetEvent, ReferenceSheetMarker } from "../../domain/plan/types";

/** 升目の状態 */
export type CalendarDayKind =
  | "saved" // 今ある日付（取り消していない）
  | "adding" // 保存すると足す
  | "removing" // 保存すると消す（取り込み・ルールの日付は「取消」の記録になる）
  | "cancelled" // 取り消した日付の記録（クリックで足し直せる）
  | "free" // 何も無い日（クリックで足す）
  | "past" // 今日より前（実績。変えられない）
  | "outside"; // シートの期間の外

export interface CalendarChanges {
  /** 足す日（YYYY-MM-DD） */
  add: Set<string>;
  /** 消す日付の id */
  remove: Set<number>;
}

export const emptyChanges = (): CalendarChanges => ({ add: new Set(), remove: new Set() });

/** 日 → 今ある日付 / 取り消した日付の記録 */
export function eventsByDate(marker: ReferenceSheetMarker): { active: Map<string, ReferenceSheetEvent>; cancelled: Map<string, ReferenceSheetEvent> } {
  const active = new Map<string, ReferenceSheetEvent>();
  const cancelled = new Map<string, ReferenceSheetEvent>();
  for (const e of marker.events) (e.is_cancelled ? cancelled : active).set(e.event_date, e);
  return { active, cancelled };
}

/** 1 日の状態（保存前の変更を含めて） */
export function dayKind(
  date: string, sheet: Pick<ReferenceSheet, "period_start" | "period_end">, today: string,
  events: ReturnType<typeof eventsByDate>, changes: CalendarChanges,
): CalendarDayKind {
  if (date < sheet.period_start || date > sheet.period_end) return "outside";
  if (date < today) return "past";
  const saved = events.active.get(date);
  if (saved) return changes.remove.has(saved.id) ? "removing" : "saved";
  if (changes.add.has(date)) return "adding";
  return events.cancelled.has(date) ? "cancelled" : "free";
}

/** 今日より前で、日付がある日か（実績として色を付ける） */
export const isPastWithDate = (date: string, today: string, events: ReturnType<typeof eventsByDate>) =>
  date < today && events.active.has(date);

/**
 * 日をクリックしたときの変更（新しい値を返す。元は変えない）: 今ある日付 → 消す（もう一度で戻す）、
 * 何も無い日・取り消した日 → 足す（もう一度で戻す）。過去・期間の外は変わらない
 */
export function toggleDay(
  date: string, sheet: Pick<ReferenceSheet, "period_start" | "period_end">, today: string,
  events: ReturnType<typeof eventsByDate>, changes: CalendarChanges,
): CalendarChanges {
  const kind = dayKind(date, sheet, today, events, changes);
  const next: CalendarChanges = { add: new Set(changes.add), remove: new Set(changes.remove) };
  const saved = events.active.get(date);
  if (kind === "saved" && saved) next.remove.add(saved.id);
  else if (kind === "removing" && saved) next.remove.delete(saved.id);
  else if (kind === "free" || kind === "cancelled") next.add.add(date);
  else if (kind === "adding") next.add.delete(date);
  return next;
}

/** まとめての API の本文（日付は昇順・id は昇順） */
export const batchBody = (changes: CalendarChanges) => ({
  add: [...changes.add].sort(),
  remove: [...changes.remove].sort((a, b) => a - b),
});

/** シートの期間に入る年（タブ） */
export function yearsOf(sheet: Pick<ReferenceSheet, "period_start" | "period_end">): number[] {
  const first = Number(sheet.period_start.slice(0, 4));
  const last = Number(sheet.period_end.slice(0, 4));
  return Array.from({ length: last - first + 1 }, (_, i) => first + i);
}

/** 開いたときの年: 今日の年（期間の外なら、近い端の年） */
export function defaultYear(sheet: Pick<ReferenceSheet, "period_start" | "period_end">, today: string): number {
  const years = yearsOf(sheet);
  const y = Number(today.slice(0, 4));
  return Math.min(Math.max(y, years[0]), years[years.length - 1]);
}

/** 年の 12 か月（YYYY-MM） */
export const monthsOfYear = (year: number) => Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`);
