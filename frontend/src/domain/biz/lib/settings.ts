import type { CompletionMode, ExecutionType, ReleaseMode } from "../types";

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
