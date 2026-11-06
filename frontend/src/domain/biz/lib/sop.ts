import type { Staff } from "../../staff/types";
import type { SopState, SopStep, SopStepCreate, SopStepUpdate, SopVersion } from "../types";

/**
 * SOP の画面のルールと表示（表示用。最終的な判定はバックエンド: src/domain_biz/services.py）。
 * 版は草稿か承認済み。承認済みの「予定 / 現行 / 過去」は日付から決まる（バックエンドの state をそのまま出す）。
 */

/** 版の作成・編集・承認・削除ができるか（業務管理者） */
export const canManageSop = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");

export const SOP_STATE_LABEL: Record<SopState, string> = {
  draft: "草稿",
  scheduled: "予定",
  current: "現行",
  past: "過去",
};

export const SOP_STATE_COLOR: Record<SopState, string> = {
  draft: "default",
  scheduled: "gold",
  current: "green",
  past: "",
};

/** バックエンドと同じ形式: v1.2.3 または v1.2.3-サフィックス */
export const VERSION_TAG_PATTERN = /^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$/;

/** 次の版のタグの候補: いちばん大きい vX.Y.Z の Y を 1 つ上げる（無ければ v1.0.0）。既にあるタグは避ける */
export function suggestNextTag(versions: readonly Pick<SopVersion, "version_tag">[]): string {
  const used = new Set(versions.map((v) => v.version_tag));
  const parsed = versions
    .map((v) => /^v(\d+)\.(\d+)\.(\d+)/.exec(v.version_tag))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => [Number(m[1]), Number(m[2]), Number(m[3])] as const)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  const top = parsed[parsed.length - 1];
  let [major, minor] = top ? [top[0], top[1] + 1] : [1, 0];
  while (used.has(`v${major}.${minor}.0`)) minor += 1;
  return `v${major}.${minor}.0`;
}

/** 新しい版が写す版（予定の版があればそれ、なければ現行。発効日時の最も新しい承認済み）。無ければ null = 空の草稿 */
export function newDraftSource(versions: readonly SopVersion[]): SopVersion | null {
  const approved = versions.filter((v) => v.status === "approved" && v.effective_at);
  approved.sort((a, b) => Date.parse(b.effective_at!) - Date.parse(a.effective_at!) || b.version_number - a.version_number);
  return approved[0] ?? null;
}

/** 画面を開いたときに選ぶ版: 現行 → 予定 → いちばん新しい版 */
export function defaultVersion(versions: readonly SopVersion[]): SopVersion | null {
  return versions.find((v) => v.state === "current") ?? versions.find((v) => v.state === "scheduled") ?? versions[0] ?? null;
}

/** ステップを含める条件の表示 */
export function conditionText(condition: Record<string, unknown>): string {
  switch (condition.type) {
    case "month_in": {
      const months = Array.isArray(condition.months) ? (condition.months as number[]) : [];
      return `${months.map((m) => `${m}月`).join("・")}のみ`;
    }
    case "first_in_week":
      return "週の最初の予定のみ";
    default:
      return "毎回";
  }
}

// ---- 草稿のステップの編集（M2.5） ----
export type ConditionType = "always" | "month_in" | "first_in_week";

export const CONDITION_LABEL: Record<ConditionType, string> = {
  always: "毎回",
  month_in: "指定した月だけ",
  first_in_week: "週の最初の予定だけ",
};

export const CONDITION_HELP: Record<ConditionType, string> = {
  always: "すべてのオーダーにこのステップを含めます。",
  month_in: "予定日の月が選んだ月のオーダーだけに含めます（例: 年次の照合は 4 月だけ）。",
  first_in_week: "その週（月〜日）で、このテンプレートの最初の予定のオーダーだけに含めます（例: 週次のまとめ）。",
};

export interface StepFormValues {
  title?: string;
  content?: string;
  condition_type?: ConditionType;
  months?: number[];
  is_blocking?: boolean;
}

type Condition = NonNullable<SopStepCreate["condition"]>;

/** フォームの条件 → 保存する条件（指定した月は重複なし・昇順） */
export function conditionBody(v: StepFormValues): Condition {
  if (v.condition_type === "month_in") return { type: "month_in", months: [...new Set(v.months ?? [])].sort((a, b) => a - b) };
  if (v.condition_type === "first_in_week") return { type: "first_in_week" };
  return { type: "always" };
}

/** 条件の入力の誤り（なければ null）。指定した月は 1 つ以上 */
export const conditionError = (v: StepFormValues): string | null =>
  v.condition_type === "month_in" && !(v.months ?? []).length ? "月を 1 つ以上選んでください" : null;

/** 保存されたステップ → フォームの初期値 */
export function stepFormValues(step: SopStep | null): StepFormValues {
  if (!step) return { condition_type: "always", months: [], is_blocking: false, content: "" };
  const type = (["month_in", "first_in_week"].includes(String(step.condition.type)) ? step.condition.type : "always") as ConditionType;
  const months = Array.isArray(step.condition.months) ? (step.condition.months as number[]) : [];
  return { title: step.title, content: step.content ?? "", condition_type: type, months, is_blocking: step.is_blocking };
}

/** 追加の本文（番号は省いて最後に付く） */
export const stepCreateBody = (v: StepFormValues): SopStepCreate => ({
  title: (v.title ?? "").trim(),
  content: (v.content ?? "").trim() || null,
  condition: conditionBody(v),
  is_blocking: v.is_blocking ?? false,
});

/** 変更の本文（変わった項目だけ。変わっていなければ null） */
export function stepUpdateBody(step: SopStep, v: StepFormValues): SopStepUpdate | null {
  const next = stepCreateBody(v);
  const body: SopStepUpdate = {};
  if (next.title !== step.title) body.title = next.title;
  if ((next.content ?? null) !== (step.content ?? null)) body.content = next.content;
  if (JSON.stringify(next.condition) !== JSON.stringify(conditionBody(stepFormValues(step)))) body.condition = next.condition;
  if (next.is_blocking !== step.is_blocking) body.is_blocking = next.is_blocking;
  return Object.keys(body).length ? body : null;
}

/** 1 つ上 / 下へ動かした後の、すべてのステップの id の並び（端なら null） */
export function movedStepIds(steps: readonly Pick<SopStep, "id">[], index: number, direction: -1 | 1): number[] | null {
  const target = index + direction;
  if (index < 0 || index >= steps.length || target < 0 || target >= steps.length) return null;
  const ids = steps.map((s) => s.id);
  [ids[index], ids[target]] = [ids[target], ids[index]];
  return ids;
}

/** 番号に抜けがあるか（削除の後など。並べ替えで 1 から振り直せる） */
export const hasNumberGaps = (steps: readonly Pick<SopStep, "step_num">[]) => steps.some((s, i) => s.step_num !== i + 1);
