import type { UIMetadataValue } from "../types";

/**
 * テンプレートの分類（定例・不定期・スペシャル）。
 * バックエンドの列ではなく ui_metadata.category に保存する（UIMetadata は未知のキーもそのまま保存する）。
 */
export const TEMPLATE_CATEGORIES = ["regular", "adhoc", "special"] as const;
export type TemplateCategory = (typeof TEMPLATE_CATEGORIES)[number];

export const CATEGORY_LABEL: Record<TemplateCategory, string> = {
  regular: "定例",
  adhoc: "不定期",
  special: "スペシャル",
};

/** 分類が未設定（分類を導入する前に作ったテンプレート）・不明な値の場合は定例として扱う */
export const DEFAULT_CATEGORY: TemplateCategory = "regular";

const isCategory = (v: unknown): v is TemplateCategory => TEMPLATE_CATEGORIES.includes(v as TemplateCategory);

export function categoryOf(ui: UIMetadataValue | undefined): TemplateCategory {
  const c = ui?.category;
  return isCategory(c) ? c : DEFAULT_CATEGORY;
}
