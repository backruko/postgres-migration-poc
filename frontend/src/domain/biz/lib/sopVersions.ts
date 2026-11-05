import type { SopVersion } from "../types";

/** バージョンがその日時に有効か（effective_at ≤ at < expired_at。expired_at が無ければ無期限。バックエンドの解決と同じ） */
export function isEffectiveAt(v: Pick<SopVersion, "effective_at" | "expired_at">, at: string): boolean {
  const t = Date.parse(at);
  if (!v.effective_at || Date.parse(v.effective_at) > t) return false;
  return !v.expired_at || Date.parse(v.expired_at) > t;
}

/**
 * オーダーに指定するバージョンの選択肢（新しい順のまま）。
 * 表示: タグ ・ 有効期間（日付は formatDate で）、予定日に有効なものには「予定日に有効」を付ける。今の指定は選べない。
 */
export function pinOptions(
  versions: SopVersion[], scheduledStart: string, pinnedId: number | null, formatDate: (iso: string) => string,
) {
  return versions.map((v) => {
    const from = v.effective_at ? formatDate(v.effective_at) : "—";
    const to = v.expired_at ? formatDate(v.expired_at) : "";
    const notes = [
      isEffectiveAt(v, scheduledStart) && "予定日に有効",
      v.id === pinnedId && "指定中",
    ].filter(Boolean);
    return {
      value: v.id,
      label: `${v.version_tag} ・ ${from} 〜 ${to}${notes.length ? `（${notes.join("・")}）` : ""}`,
      disabled: v.id === pinnedId,
    };
  });
}
