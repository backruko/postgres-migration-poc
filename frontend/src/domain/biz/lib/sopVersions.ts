import type { SopVersion } from "../types";

/** バージョンがその日時に有効か（effective_at ≤ at < expired_at。expired_at が無ければ無期限。バックエンドの解決と同じ） */
export function isEffectiveAt(v: Pick<SopVersion, "effective_at" | "expired_at">, at: string): boolean {
  const t = Date.parse(at);
  if (!v.effective_at || Date.parse(v.effective_at) > t) return false;
  return !v.expired_at || Date.parse(v.expired_at) > t;
}

/**
 * 有効期間の表示「2026/11/2 〜 2026/11/30」（終わりは最後の日。expired_at は次の版の発効日時で、その日は含まない）。
 * 終わりの無い版は「2026/11/2 〜」、草稿は「—」。formatDate は ISO 日時を日付の文字列にする（画面のタイムゾーンで）
 */
export function periodText(v: Pick<SopVersion, "effective_at" | "expired_at">, formatDate: (iso: string) => string): string {
  if (!v.effective_at) return "—";
  const from = formatDate(v.effective_at);
  if (!v.expired_at) return `${from} 〜`;
  return `${from} 〜 ${formatDate(new Date(Date.parse(v.expired_at) - 1).toISOString())}`;
}

/**
 * オーダーに指定するバージョンの選択肢（新しい順のまま）。
 * 表示: タグ ・ 有効期間（日付は formatDate で）、予定日に有効なものには「予定日に有効」を付ける。今の指定は選べない。
 */
export function pinOptions(
  versions: SopVersion[], scheduledStart: string, pinnedId: number | null, formatDate: (iso: string) => string,
) {
  return versions.map((v) => {
    const notes = [
      isEffectiveAt(v, scheduledStart) && "予定日に有効",
      v.id === pinnedId && "指定中",
    ].filter(Boolean);
    return {
      value: v.id,
      label: `${v.version_tag} ・ ${periodText(v, formatDate)}${notes.length ? `（${notes.join("・")}）` : ""}`,
      disabled: v.id === pinnedId,
    };
  });
}
