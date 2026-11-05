import type { Staff } from "../../staff/types";

/**
 * 予定 1 件の手動操作（変更・取消・臨時・ルールに戻す・取消の復元）ができるか。どれもオーダーを動かす・取り消す・作るため、
 * オーダーの取消・予定日前の開始の承認と同じくリリース担当・業務管理者だけ（作業者はカレンダーを見るだけ）。
 * 最終判定はバックエンドの require_roles。
 */
export const canChangeOccurrences = (s: Staff | null | undefined) =>
  s?.status === "active" && s.roles.some((r) => r === "releaser" || r === "admin");
