import type { Staff } from "../../staff/types";
import type { CompletionMode, ExecutionType, ReleaseMode } from "../types";

/**
 * 予定テンプレートと業務の設定を変更できるか（業務管理者のみ。参照は誰でも。最終判定はバックエンドの require_roles）
 */
export const canEditPlanSettings = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");

/**
 * 変更の内容を「確認」できるか（業務管理者・リリース担当）。確認は何も保存しない:
 * テンプレートの「何が変わるか」の確認と、シートの取り込みの「取り込むと何が変わるか」の表示。
 * リリース担当は準備に加わり、保存・作成・取り込みは業務管理者が行う（2026-11-10。下書きと承認の仕組みができるまでの形）
 */
export const canPreviewPlanSettings = (s: Staff | null | undefined) =>
  s?.status === "active" && (s.roles.includes("admin") || s.roles.includes("releaser"));

/**
 * 業務のリリース設定（方法・時刻・何日前）を変えられるか（リリース担当・業務管理者。細かな権限 P.5、2026-11-11）。
 * ほかの業務の設定は業務管理者のみ（canEditPlanSettings）
 */
export const canEditReleaseSettings = (s: Staff | null | undefined) =>
  s?.status === "active" && (s.roles.includes("admin") || s.roles.includes("releaser"));

/** リリース設定の表示（例: 自動: 予定日の1日前 05:30）。履歴の変更前・変更後にも使う */
export function releaseText(v: { release_mode: ReleaseMode; release_time: string; release_days_before: number }): string {
  return v.release_mode === "automatic"
    ? `${RELEASE_MODE_LABEL.automatic}: 予定日の${v.release_days_before ? `${v.release_days_before}日前` : "当日"} ${v.release_time.slice(0, 5)}`
    : RELEASE_MODE_LABEL.manual_approval;
}

/** 変更できない職員に見せる案内 */
export const PLAN_SETTINGS_ADMIN_ONLY = "変更は業務管理者だけができます";
/** 確認だけできる職員（リリース担当）に見せる案内 */
export const PLAN_SETTINGS_PREVIEW_ONLY = "変更は業務管理者だけができます（リリース担当は、内容の確認まで）";
/** 確認だけの画面に出す説明 */
export const PREVIEW_ONLY_NOTE =
  "確認だけができます。ここで入力した内容は保存されません。この内容でよければ、業務管理者に伝えてください（保存は業務管理者が行います）。";

export const RELEASE_MODE_LABEL: Record<ReleaseMode, string> = {
  manual_approval: "手動（リリース担当が承認）",
  automatic: "自動（指定の時刻に）",
};

export const EXECUTION_TYPE_LABEL: Record<ExecutionType, string> = {
  sop: "SOP のステップで作業する",
  lightweight: "顧客側の出来事（ステップなし）",
};

export const COMPLETION_MODE_LABEL: Record<CompletionMode, string> = {
  manual: "作業者が受領を記録する",
  automatic: "信号で自動的に完了する",
};

export interface ClientEventValues {
  execution_type: ExecutionType;
  completion_mode: CompletionMode;
  signal_type?: string | null;
}

/**
 * 顧客側の出来事の設定の組み合わせの誤り（バックエンドと同じ規則。表示用）。
 * 信号での完了は LIGHTWEIGHT のみ・信号の種類が必須。null なら問題なし
 */
export function clientEventError(v: ClientEventValues): string | null {
  if (v.completion_mode === "automatic" && v.execution_type !== "lightweight") return "信号での完了は「顧客側の出来事」の業務だけで使えます";
  if (v.completion_mode === "automatic" && !v.signal_type?.trim()) return "信号の種類を入力してください";
  return null;
}

/** 送る値（信号で完了しないときは信号の種類を外す） */
export function clientEventBody(v: ClientEventValues) {
  const automatic = v.completion_mode === "automatic" && v.execution_type === "lightweight";
  return {
    execution_type: v.execution_type,
    completion_mode: automatic ? ("automatic" as const) : ("manual" as const),
    signal_type: automatic ? (v.signal_type ?? "").trim() : null,
  };
}
