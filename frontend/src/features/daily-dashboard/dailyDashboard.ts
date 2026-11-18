import type { DailyCalendarStatus, DailyDashboard, DailyMilestone, VoidReason } from "../../domain/ops/types";
import { dayjs, inZone } from "../../shared/lib/datetime";

// ---------------- 日付（"YYYY-MM-DD" 文字列で扱う） ----------------

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** URL の ?date= を検証する（不正・未指定なら今日） */
export function parseDateParam(value: string | null, today: string): string {
  return value && ISO_DATE.test(value) && dayjs(value, "YYYY-MM-DD", true).isValid() ? value : today;
}

export const addDays = (date: string, days: number) => dayjs(date, "YYYY-MM-DD").add(days, "day").format("YYYY-MM-DD");

/** 例: 2026年11月3日（火） */
export const dayLabel = (date: string) => dayjs(date, "YYYY-MM-DD").format("YYYY年M月D日（dd）");

/** 例: 11/3 */
export const shortDate = (date: string) => dayjs(date, "YYYY-MM-DD").format("M/D");

// ---------------- 表示用ラベル ----------------

/** 予定が無効化された理由（バックエンドの void_reason） */
export const VOID_REASON_LABEL: Record<VoidReason, string> = {
  template_revised: "テンプレートの変更",
  template_deleted: "テンプレートの削除",
  source_date_changed: "元の日付の変更",
  manual: "人が変更・取消",
  reverted: "ルールに戻した",
  calendar_changed: "休日の変更",
};

/** シート日付の出どころ（年間計画シート画面と同じ言葉） */
export function milestoneSource(m: DailyMilestone): string {
  const fromSource = (m.rule_date ?? m.original_date ?? null) !== null;
  if (m.is_cancelled) return "取消";
  if (m.is_manual) return fromSource ? "人が変更" : "人が追加";
  return m.rule_date ? "ルール" : "取り込み";
}

/** 営業日カレンダーでのその日の扱い */
export function calendarDayLabel(c: DailyCalendarStatus): string {
  if (c.is_business_day) return "営業日";
  return c.holiday_name ? `休日（${c.holiday_name}）` : "休業日（週末）";
}

// ---------------- 集計 ----------------

export function summarize(d: DailyDashboard) {
  const businesses = d.clients.flatMap((c) => c.businesses);
  return {
    clients: d.clients.length,
    occurrences: d.total,
    voided: businesses.reduce((n, b) => n + b.voided.length, 0),
    milestones: d.clients.reduce((n, c) => n + c.milestones.length, 0),
  };
}

// ---------------- 締切（N.4） ----------------

/**
 * 締切の表示: 基準の日（YYYY-MM-DD、表示しているタイムゾーンで）と同じ日なら「15:00」、違えば「11/4 15:00」。
 * 日次ダッシュボードの行では予定の日、上の一覧では今日を基準にする。
 */
export function deadlineLabel(deadlineIso: string, baseDay: string, tz: string): string {
  const d = inZone(deadlineIso, tz);
  return d.format("YYYY-MM-DD") === baseDay ? d.format("HH:mm") : d.format("M/D HH:mm");
}

/** 行に締切を出すか（予定の終了と同じなら出さない: 予定の時刻の表示で分かる） */
export const showsOwnDeadline = (deadlineIso: string, endsIso: string) => Date.parse(deadlineIso) !== Date.parse(endsIso);
