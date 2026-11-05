import type { Staff } from "../../staff/types";
import type { BizDependency, BizDependencyCreate, DependencyKind, DependencyMatchRule } from "../types";

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

/** 直前の上流の「何日前まで」の初期値（月次の上流を毎日の業務が待つ想定: 1か月 + 余裕） */
export const DEFAULT_LOOKBACK_DAYS = 35;
export const LOOKBACK_RANGE = { min: 1, max: 366 } as const;

/** 一覧の「突き合わせ」の表示（直前の上流は日数も） */
export const matchRuleText = (d: Pick<BizDependency, "match_rule" | "lookback_days">) =>
  d.match_rule === "latest_before" && d.lookback_days ? `${MATCH_RULE_LABEL.latest_before}（${d.lookback_days}日前まで）` : MATCH_RULE_LABEL[d.match_rule];

/** 追加フォームの値 → 作成の本文（日数は直前の上流のときだけ送る。バックエンドは他のルールの日数を拒否する） */
export function dependencyBody(v: {
  upstream_biz_id: number; match_rule: DependencyMatchRule; kind: DependencyKind; lookback_days?: number | null;
}): BizDependencyCreate {
  const body: BizDependencyCreate = { upstream_biz_id: v.upstream_biz_id, match_rule: v.match_rule, kind: v.kind };
  if (v.match_rule === "latest_before") body.lookback_days = v.lookback_days ?? DEFAULT_LOOKBACK_DAYS;
  return body;
}

/** 定義の作成・削除ができるか（業務管理者のみ。最終判定はバックエンド） */
export const canManageDependencies = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");
