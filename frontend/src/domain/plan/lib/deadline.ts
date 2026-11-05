// テンプレートの締切（N.1 / N.4）の表示用ラベルと説明。UI から独立した純粋な処理。
import type { DeadlineMode, PlanTemplate } from "../types";

export const DEADLINE_MODE_LABEL: Record<DeadlineMode, string> = {
  end: "予定の終了",
  offset: "開始から",
  fixed_time: "固定の時刻",
};

export const DEADLINE_MODE_HELP: Record<DeadlineMode, string> = {
  end: "開始時刻 + 所要時間（これまでどおり）",
  offset: "予定の開始から何分後（所要時間を超えてよい。社内の工程向け。予定が動けば締切も動く）",
  fixed_time: "予定日の何日後の何時（外部との受け渡しの締切向け。同じ日の中で予定が動いても締切は動かない）",
};

/** 「何日後」の選択肢（0〜7） */
export const DAY_OFFSET_OPTIONS = Array.from({ length: 8 }, (_, d) => ({ value: d, label: dayOffsetLabel(d) }));

export function dayOffsetLabel(days: number): string {
  return days === 0 ? "当日" : days === 1 ? "翌日" : `${days}日後`;
}

/** 分を「5時間30分」のように */
export function minutesLabel(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return [h ? `${h}時間` : "", m ? `${m}分` : ""].join("") || "0分";
}

/** テンプレートの締切の説明（一覧・確認用）。例: 「固定の時刻 15:00（翌日）・30分前に知らせる」 */
export function describeDeadline(
  t: Pick<PlanTemplate, "deadline_mode" | "deadline_offset_minutes" | "deadline_time" | "deadline_day_offset" | "warn_before_minutes">,
): string {
  let rule: string;
  if (t.deadline_mode === "offset" && t.deadline_offset_minutes) {
    rule = `開始から${minutesLabel(t.deadline_offset_minutes)}後`;
  } else if (t.deadline_mode === "fixed_time" && t.deadline_time) {
    rule = `${t.deadline_time.slice(0, 5)}（${dayOffsetLabel(t.deadline_day_offset ?? 0)}）`;
  } else {
    rule = DEADLINE_MODE_LABEL.end;
  }
  return t.warn_before_minutes ? `${rule}・${minutesLabel(t.warn_before_minutes)}前に知らせる` : rule;
}
