import type { Staff } from "../../staff/types";
import type { SopState, SopVersion } from "../types";

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
