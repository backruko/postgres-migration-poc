// Ant Design の ColorPicker をフォームで使うための共通設定

/** 表示色の候補 */
export const COLOR_PRESETS = [
  { label: "よく使う色", colors: ["#1f6f5c", "#52aecd", "#2a5db0", "#7b5ea7", "#d9822b", "#c8342a", "#FF0000", "#7a8781"] },
];

/**
 * Form.Item の getValueFromEvent 用: ColorPicker の値を "#rrggbb" で取り出す。
 * onChange の第2引数は CSS 文字列（"rgb(82,174,205)"）のため使わない
 * （バックエンドの UIMetadata.bg_color は #RGB / #RRGGBB / #RRGGBBAA のみ受け付ける）。
 */
export const hexFromColorPicker = (color: { toHexString(): string; cleared: boolean }) =>
  color.cleared ? undefined : color.toHexString();
