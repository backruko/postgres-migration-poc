import type { MarkerInUseDetails } from "../types";

/**
 * 年間計画シートの画面のルール（表示用。最終的な判定はバックエンド: src/domain_plan/services.py の delete_marker）。
 */

const isTemplateRefs = (v: unknown): v is { id: number; name: string }[] =>
  Array.isArray(v) && v.every((t) => t && typeof t === "object" && typeof t.id === "number" && typeof t.name === "string");

/** サーバーの details が、マーカーを削除できない理由の形か（形が違えば null。英語のメッセージを出す） */
export function asMarkerInUseDetails(details: Record<string, unknown> | null | undefined): MarkerInUseDetails | null {
  if (!details) return null;
  const { marker, templates, deleted_templates, draft_change_ids } = details;
  if (typeof marker !== "string" || !isTemplateRefs(templates) || !isTemplateRefs(deleted_templates)) return null;
  if (!Array.isArray(draft_change_ids) || !draft_change_ids.every((id) => typeof id === "number")) return null;
  return { marker, templates, deleted_templates, draft_change_ids };
}

const names = (refs: { id: number; name: string }[]) => refs.map((t) => `${t.name}（#${t.id}）`).join("、");

/**
 * マーカーを削除できない理由の日本語（例: マーカー「受領」は使われているため削除できません: テンプレート 毎日（#12）で
 * 使用中 ／ 削除したテンプレート 旧（#3）が参照（元に戻せるため）／ 承認待ちの下書き 1 件）
 */
export function markerInUseText(d: MarkerInUseDetails): string {
  const reasons: string[] = [];
  if (d.templates.length) reasons.push(`テンプレート ${names(d.templates)} で使用中`);
  if (d.deleted_templates.length) reasons.push(`削除したテンプレート ${names(d.deleted_templates)} が参照（元に戻せるため）`);
  if (d.draft_change_ids.length) reasons.push(`承認待ちの下書き ${d.draft_change_ids.length} 件が指定`);
  return `マーカー「${d.marker}」は使われているため削除できません: ${reasons.join(" ／ ")}`;
}
