import type { RoleCode, Staff } from "../../staff/types";
import type { ConflictAction, OrderDetail, OrderEventType, OrderStatus } from "../types";

// ---------------- 表示用ラベル ----------------

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  pending: "未リリース",
  ready: "リリース済",
  in_progress: "作業中",
  on_hold: "保留中",
  finished: "完了",
  canceled: "取消",
};

export const ORDER_STATUS_COLOR: Record<OrderStatus, string | undefined> = {
  pending: undefined,
  ready: "blue",
  in_progress: "orange",
  on_hold: "gold",
  finished: "green",
  canceled: "default",
};

export const ORDER_EVENT_LABEL: Record<OrderEventType, string> = {
  created: "作成",
  status_changed: "状態の変更",
  rescheduled: "日時の変更",
  pinned: "SOP バージョンの指定",
  unpinned: "SOP バージョンの指定を解除",
  early_start_approved: "予定日前の開始を承認",
  conflict_raised: "矛盾",
  conflict_resolved: "矛盾の解決",
};

export const SOP_SOURCE_LABEL = { frozen: "固定", pinned: "指定", effective: "予定日に有効" } as const;

export const CONFLICT_ACTION_LABEL: Record<ConflictAction, string> = {
  accept_plan: "予定の新しい日時を採用",
  keep: "このまま続ける",
  cancel: "オーダーを取り消す",
};

// ---------------- 操作できるか（バックエンドの @require_roles と同じ組み合わせ。最終判定はバックエンド） ----------------

export type OrderAction = "release" | "approveEarlyStart" | "start" | "hold" | "resume" | "finish" | "cancel" | "resolveConflict";

const OPERATOR: RoleCode[] = ["operator"];
const HOLDERS: RoleCode[] = ["operator", "releaser", "admin"];
const PRIVILEGED: RoleCode[] = ["releaser", "admin"];

const ROLES: Record<OrderAction, RoleCode[]> = {
  release: PRIVILEGED,
  approveEarlyStart: ["releaser"],
  start: OPERATOR,
  hold: HOLDERS,
  resume: HOLDERS,
  finish: OPERATOR,
  cancel: PRIVILEGED,
  resolveConflict: PRIVILEGED,
};

export const ORDER_ACTION_LABEL: Record<OrderAction, string> = {
  release: "リリース",
  approveEarlyStart: "予定日前の開始を承認",
  start: "開始",
  hold: "保留",
  resume: "再開",
  finish: "完了",
  cancel: "取消",
  resolveConflict: "矛盾を解決",
};

/** 理由の入力が必須の操作 */
export const REASON_REQUIRED: ReadonlySet<OrderAction> = new Set(["hold", "cancel"]);

/** 予定日（日本時間の日付 YYYY-MM-DD）。today と同じ形式で比べる */
export const scheduledDay = (order: Pick<OrderDetail, "scheduled_start">, toLocalDate: (iso: string) => string) =>
  toLocalDate(order.scheduled_start);

/**
 * 今の状態で表示する操作（状態と、操作する職員のロールで決める）。
 * today / day は日本時間の YYYY-MM-DD。予定日前の開始は承認済みのときだけ「開始」を出す。
 */
export function availableActions(order: OrderDetail, staff: Staff | null | undefined, today: string, day: string): OrderAction[] {
  if (!staff || staff.status !== "active") return [];
  const can = (a: OrderAction) => staff.roles.some((r) => ROLES[a].includes(r));
  const actions: OrderAction[] = [];
  const s = order.status;
  if (order.conflict) actions.push("resolveConflict");
  if (s === "pending" && !order.conflict) actions.push("release");
  if ((s === "pending" || s === "ready") && day > today && !order.early_start_approved) actions.push("approveEarlyStart");
  if (s === "ready" && !order.conflict && (day <= today || order.early_start_approved)) actions.push("start");
  if (s === "pending" || s === "ready" || s === "in_progress") actions.push("hold");
  if (s === "on_hold") actions.push("resume");
  if (s === "in_progress") actions.push("finish");
  if (s !== "finished" && s !== "canceled") actions.push("cancel");
  return actions.filter(can);
}

/** 開始できない理由（ready なのに「開始」が出ない場合の説明） */
export function startBlockedReason(order: OrderDetail, today: string, day: string): string | null {
  if (order.status !== "ready") return null;
  if (order.conflict) return "矛盾を解決するまで開始できません";
  if (day > today && !order.early_start_approved) return `予定日（${day}）より前の開始には、リリース担当の承認が必要です`;
  return null;
}
