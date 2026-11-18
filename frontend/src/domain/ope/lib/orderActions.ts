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
  dependency_override_approved: "依存関係を待たない開始を承認",
};

export const SOP_SOURCE_LABEL = { frozen: "固定", pinned: "指定", effective: "予定日に有効" } as const;

export const CONFLICT_ACTION_LABEL: Record<ConflictAction, string> = {
  accept_plan: "予定の新しい日時を採用",
  keep: "このまま続ける",
  cancel: "オーダーを取り消す",
};

// ---------------- 操作できるか（バックエンドの @require_roles と同じ組み合わせ。最終判定はバックエンド） ----------------

export type OrderAction =
  | "release" | "approveEarlyStart" | "approveDependencyOverride" | "start" | "hold" | "resume" | "finish" | "cancel"
  | "resolveConflict" | "pin" | "unpin";

const OPERATOR: RoleCode[] = ["operator"];
const HOLDERS: RoleCode[] = ["operator", "releaser", "admin"];
const PRIVILEGED: RoleCode[] = ["releaser", "admin"];

const ROLES: Record<OrderAction, RoleCode[]> = {
  release: PRIVILEGED,
  approveEarlyStart: ["releaser"],
  approveDependencyOverride: PRIVILEGED,
  start: OPERATOR,
  hold: HOLDERS,
  resume: HOLDERS,
  finish: OPERATOR,
  cancel: PRIVILEGED,
  resolveConflict: PRIVILEGED,
  pin: PRIVILEGED,
  unpin: PRIVILEGED,
};

export const ORDER_ACTION_LABEL: Record<OrderAction, string> = {
  release: "リリース",
  approveEarlyStart: "予定日前の開始を承認",
  approveDependencyOverride: "依存関係を待たずに開始を承認",
  start: "開始",
  hold: "保留",
  resume: "再開",
  finish: "完了",
  cancel: "取消",
  resolveConflict: "矛盾を解決",
  pin: "SOP バージョンを指定",
  unpin: "SOP バージョンの指定を解除",
};

/** 理由の入力が必須の操作 */
export const REASON_REQUIRED: ReadonlySet<OrderAction> = new Set(["hold", "cancel", "approveDependencyOverride"]);

/** 完了の操作の表示名（顧客側の出来事は「受領」） */
export const finishLabel = (order: Pick<OrderDetail, "execution_type">) => (order.execution_type === "lightweight" ? "受領" : "完了");

/** 完了の操作に理由が必須か（信号で完了する業務を手で完了にするとき） */
export const finishNeedsReason = (order: Pick<OrderDetail, "completion_mode">) => order.completion_mode === "automatic";

/** 開始を止めている依存関係（hard・上流が未完了・承認なし） */
export const blockingDependencies = (order: Pick<OrderDetail, "dependencies">) =>
  order.dependencies.filter((d) => d.kind === "hard" && !d.met && !d.overridden);

/** 予定日（日本時間の日付 YYYY-MM-DD）。today と同じ形式で比べる */
export const scheduledDay = (order: Pick<OrderDetail, "scheduled_start">, toLocalDate: (iso: string) => string) =>
  toLocalDate(order.scheduled_start);

/**
 * 今の状態で表示する操作（状態と、ログインしている職員のロールで決める）。
 * today / day は日本時間の YYYY-MM-DD。予定日前の開始は承認済みのときだけ「開始」を出す。
 */
export function availableActions(order: OrderDetail, staff: Staff | null | undefined, today: string, day: string): OrderAction[] {
  if (!staff || staff.status !== "active") return [];
  const can = (a: OrderAction) => staff.roles.some((r) => ROLES[a].includes(r));
  const actions: OrderAction[] = [];
  const s = order.status;
  if (order.conflict) actions.push("resolveConflict");
  if (s === "pending" && !order.conflict) actions.push("release");
  const blocked = blockingDependencies(order).length > 0;
  if ((s === "pending" || s === "ready") && day > today && !order.early_start_approved) actions.push("approveEarlyStart");
  if ((s === "pending" || s === "ready") && blocked) actions.push("approveDependencyOverride");
  if (s === "ready" && !order.conflict && !blocked && (day <= today || order.early_start_approved)) actions.push("start");
  if (s === "pending" || s === "ready" || s === "in_progress") actions.push("hold");
  if (s === "on_hold") actions.push("resume");
  // ステップのあるオーダーは最後の承認で自動で完了する（手動の完了はステップの無いオーダーのみ）。
  // 顧客側の出来事（LIGHTWEIGHT）は ready からも完了（受領）にできる
  if (order.steps.length === 0 && (s === "in_progress" || (s === "ready" && order.execution_type === "lightweight"))) actions.push("finish");
  if (s !== "finished" && s !== "canceled") actions.push("cancel");
  // SOP バージョンの指定はリリース前のみ（リリースで固定される）
  if (s === "pending") actions.push("pin");
  if (s === "pending" && order.pinned_sop_version_id !== null) actions.push("unpin");
  return actions.filter(can);
}

/** 開始できない理由（ready なのに「開始」が出ない場合の説明） */
export function startBlockedReason(order: OrderDetail, today: string, day: string): string | null {
  if (order.status !== "ready") return null;
  if (order.conflict) return "矛盾を解決するまで開始できません";
  if (day > today && !order.early_start_approved) return `予定日（${day}）より前の開始には、リリース担当の承認が必要です`;
  if (blockingDependencies(order).length) return "上流のオーダーが完了するまで開始できません（リリース担当・業務管理者が承認すれば開始できます）";
  return null;
}
