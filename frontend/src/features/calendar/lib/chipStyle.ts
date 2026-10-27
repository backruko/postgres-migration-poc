import type { UIMetadataValue } from "../../../domain/plan/types";

/** ui_metadata（テンプレート + インスタンスの実効値）から作る表示スタイル */
export interface ChipStyle {
  background: string;
  color: string;
  icon: string;
  bold: boolean;
}

const FALLBACK_BG = "#7a8781";

/** 背景色に対して読みやすい文字色（WCAG の相対輝度で判定） */
export function readableTextOn(hex: string): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h.slice(0, 6);
  if (!/^[0-9a-f]{6}$/i.test(full)) return "#ffffff";
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.33 ? "#10201b" : "#ffffff";
}

export function chipStyle(ui: UIMetadataValue | undefined): ChipStyle {
  const background = typeof ui?.bg_color === "string" ? ui.bg_color : FALLBACK_BG;
  return {
    background,
    color: typeof ui?.text_color === "string" ? ui.text_color : readableTextOn(background),
    icon: typeof ui?.icon === "string" ? ui.icon : "dot",
    bold: ui?.is_bold === true,
  };
}
