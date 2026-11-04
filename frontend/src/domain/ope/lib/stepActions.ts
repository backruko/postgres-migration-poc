import type { RoleCode, Staff } from "../../staff/types";
import type { OrderDetail, OrderStep, StepEvent, StepState } from "../types";

// ---------------- 表示用ラベル ----------------

// オーダーの状態（作業中など）と混ざらないよう、ステップは別の言葉にする
export const STEP_STATE_LABEL: Record<StepState, string> = {
  locked: "待機",
  todo: "未提出",
  submitted: "確認待ち",
  approved: "承認済",
};

/** ステップの記録1件の説明（提出・承認・差し戻し・開く・作成・外れた） */
export function describeStepEvent(e: StepEvent): string {
  if (e.event_type === "created") return "作成";
  if (e.event_type === "retired") return "作り直しで外れた";
  if (e.to_state === "submitted") return "提出";
  if (e.to_state === "approved") return "承認";
  if (e.from_state === "submitted" && e.to_state === "todo") return "差し戻し";
  if (e.from_state === "locked" && e.to_state === "todo") return "開始可能になった";
  return "状態の変更";
}

export const STEP_STATE_COLOR: Record<StepState, string | undefined> = {
  locked: undefined,
  todo: "orange",
  submitted: "blue",
  approved: "green",
};

// ---------------- 操作できるか（バックエンドの @require_roles と同じ組み合わせ。最終判定はバックエンド） ----------------

export type StepAction = "submit" | "approve" | "reject";

const ROLES: Record<StepAction, RoleCode[]> = {
  submit: ["operator"],
  approve: ["checker"],
  reject: ["checker"],
};

export const STEP_ACTION_LABEL: Record<StepAction, string> = {
  submit: "提出",
  approve: "承認",
  reject: "差し戻し",
};

/** 理由の入力が必須の操作 */
export const STEP_REASON_REQUIRED: ReadonlySet<StepAction> = new Set(["reject"]);

/** ステップを最後に提出した職員（四つの目: この人は承認・差し戻しできない） */
export function latestSubmitter(step: OrderStep): number | null {
  const submitted = step.events.filter((e) => e.to_state === "submitted");
  return submitted.length ? submitted[submitted.length - 1].actor_staff_id : null;
}

/** 最後の差し戻しの理由（今 todo で、直前が差し戻しのときだけ） */
export function rejectionReason(step: OrderStep): string | null {
  const last = step.events[step.events.length - 1];
  return step.state === "todo" && last?.from_state === "submitted" && last.to_state === "todo" ? last.reason : null;
}

/** ステップの操作（作業中のオーダーのみ。保留中は止まる）。提出者本人には承認・差し戻しを出さない */
export function availableStepActions(order: OrderDetail, step: OrderStep, staff: Staff | null | undefined): StepAction[] {
  if (!staff || staff.status !== "active" || order.status !== "in_progress" || step.id === null) return [];
  const can = (a: StepAction) => staff.roles.some((r) => ROLES[a].includes(r));
  const actions: StepAction[] = [];
  if (step.state === "todo") actions.push("submit");
  if (step.state === "submitted" && latestSubmitter(step) !== staff.id) actions.push("approve", "reject");
  return actions.filter(can);
}

/** 承認・差し戻しが出ない理由（確認者が自分の提出したステップを見ているとき） */
export function stepBlockedReason(order: OrderDetail, step: OrderStep, staff: Staff | null | undefined): string | null {
  if (!staff || order.status !== "in_progress" || step.state !== "submitted") return null;
  if (staff.roles.includes("checker") && latestSubmitter(step) === staff.id) return "提出した本人は承認・差し戻しできません（別の確認者が行います）";
  return null;
}

/** ステップの進み具合（例: "1/3"）。ステップの無いオーダーは null */
export function stepProgress(total: number, approved: number): string | null {
  return total > 0 ? `${approved}/${total}` : null;
}
