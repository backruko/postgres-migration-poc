import type { Staff } from "../../staff/types";
import type { DependencyKind, DependencyMatchRule } from "../types";

export const MATCH_RULE_LABEL: Record<DependencyMatchRule, string> = {
  same_sheet_event: "同じシート日付",
  same_occurrence_date: "同じ日付",
  latest_before: "直前の上流",
};

export const MATCH_RULE_HELP: Record<DependencyMatchRule, string> = {
  same_sheet_event: "同じ基準日（例: 振込日）から算出した予定どうし。シート由来でない予定には当てはまりません",
  same_occurrence_date: "ルール上の同じ日付の予定どうし（休日で動いても元の日付で突き合わせます）",
  latest_before: "下流の予定開始以前で最新の上流の予定（周期が違う業務どうし。例: 月次の受領 → 毎日の処理）",
};

export const KIND_LABEL: Record<DependencyKind, string> = {
  hard: "待つ",
  informational: "参考",
};

export const KIND_HELP: Record<DependencyKind, string> = {
  hard: "上流のオーダーが完了するまで開始できません（リリース担当・業務管理者の承認で開始できます）",
  informational: "表示だけ（開始は止めません）",
};

/** 定義の作成・削除ができるか（業務管理者のみ。最終判定はバックエンド） */
export const canManageDependencies = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");
