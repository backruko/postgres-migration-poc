import type { RoleCode, Staff, StaffStatus } from "../types";

/**
 * 画面の出し分け用の権限（ボタンを隠す・無効にするだけ。最終的な判定はバックエンドの require_roles）。
 * バックエンドの src/domain_staff/router.py と同じ組み合わせにそろえる。
 */
const STAFF_READERS: RoleCode[] = ["system_admin", "admin", "auditor"];

const isActive = (s: Staff | null | undefined): s is Staff => s?.status === "active";
const hasAny = (s: Staff, roles: RoleCode[]) => s.roles.some((r) => roles.includes(r));

/** 職員の一覧を見られるか */
export const canReadStaff = (s: Staff | null | undefined) => isActive(s) && hasAny(s, STAFF_READERS);

/** 職員の登録・変更ができるか */
export const canManageStaff = (s: Staff | null | undefined) => isActive(s) && hasAny(s, ["system_admin"]);

export const STATUS_LABEL: Record<StaffStatus, string> = {
  pending: "利用開始前",
  active: "有効",
  suspended: "利用停止中",
};

export const STATUS_COLOR: Record<StaffStatus, string> = {
  pending: "gold",
  active: "green",
  suspended: "red",
};
