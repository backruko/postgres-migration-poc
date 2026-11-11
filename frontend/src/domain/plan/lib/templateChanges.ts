// テンプレートの変更依頼（下書き → 承認 → 反映。Level 2）の表示用ラベルと、画面の出し分け。UI から独立した純粋な処理。
// 出し分けはボタンを隠す・無効にするだけ。最終的な判定はバックエンド（src/domain_plan/template_changes.py）。
import { ApiError } from "../../../shared/api/http";
import type { Staff } from "../../staff/types";
import type { PlanTemplate, TemplateChange, TemplateChangeKind, TemplateChangeStatus } from "../types";

export const CHANGE_STATUS_LABEL: Record<TemplateChangeStatus, string> = {
  draft: "承認待ち",
  approved: "承認済み",
  rejected: "差し戻し",
  withdrawn: "取り下げ",
};

export const CHANGE_STATUS_COLOR: Record<TemplateChangeStatus, string> = {
  draft: "gold",
  approved: "green",
  rejected: "red",
  withdrawn: "default",
};

export const CHANGE_KIND_LABEL: Record<TemplateChangeKind, string> = {
  create: "新規作成",
  update: "変更",
  delete: "削除",
};

const isActive = (s: Staff | null | undefined): s is Staff => s?.status === "active";

/**
 * テンプレートを変えるときの形（2026-11-11 の決定）。
 * - direct: 業務管理者。これまでどおり直接保存する（自分の下書きを自分で承認できるため、下書きは手間が増えるだけ）
 * - draft: 業務管理者でないリリース担当。下書きとして保存し、業務管理者の承認を待つ
 * - none: それ以外（見るだけ）
 */
export type TemplateWriteMode = "direct" | "draft" | "none";

export function templateWriteMode(s: Staff | null | undefined): TemplateWriteMode {
  if (!isActive(s)) return "none";
  if (s.roles.includes("admin")) return "direct";
  if (s.roles.includes("releaser")) return "draft";
  return "none";
}

/** 下書きを直す・取り下げることができるか（承認待ちの、自分が書いた下書きだけ） */
export function canChangeOwnDraft(change: TemplateChange, s: Staff | null | undefined): boolean {
  return isActive(s) && change.status === "draft" && change.drafted_by_id === s.id
    && (s.roles.includes("releaser") || s.roles.includes("admin"));
}

/** 承認待ちの下書きがあるテンプレートは、承認・差し戻し・取り下げまで、だれも直接変えられない（バックエンドは 409） */
export function pendingDraftReason(t: Pick<PlanTemplate, "pending_change_id">): string | null {
  return t.pending_change_id == null
    ? null
    : "承認待ちの下書きがあります。承認・差し戻し・取り下げが済むまで、このテンプレートは変更できません。";
}

/** 決まってから days 日以内か（変更依頼の欄に「最近の自分の下書き」として出す範囲） */
export function decidedWithin(change: TemplateChange, now: Date, days = 30): boolean {
  if (!change.decided_at) return false;
  return now.getTime() - new Date(change.decided_at).getTime() <= days * 24 * 60 * 60 * 1000;
}

/** 変更依頼の API のエラーを、画面に出す日本語に（知らないコードはサーバーのメッセージのまま） */
export function draftErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "通信に失敗しました。";
  switch (e.code) {
    case "TEMPLATE_DRAFT_PENDING":
      return "このテンプレートには、すでに承認待ちの下書きがあります。承認・差し戻し・取り下げが済むまで、変更も新しい下書きもできません。";
    case "TEMPLATE_CHANGE_DECIDED":
      return "この下書きは、すでに承認・差し戻し・取り下げが済んでいます。画面を読み直してください。";
    case "TEMPLATE_CHANGED_MEANWHILE":
      return "確かめている間にテンプレートが変わりました。もう一度保存してください。";
    case "FORBIDDEN":
      return "この操作はできません（下書きを直す・取り下げるのは、書いた本人だけです）。";
    default:
      return e.status === 400 && e.message.includes("changes nothing")
        ? "変更がありません（今の設定と同じです）。"
        : e.message;
  }
}
