import type { CreateStaffInput, RoleCode, Staff, StaffStatus, UpdateStaffInput } from "../../domain/staff/types";

/** フォームの値（登録・編集で共通） */
export interface StaffFormValues {
  login_id?: string;
  name?: string;
  email?: string;
  roles?: RoleCode[];
}

const blankToNull = (v: string | undefined) => (v?.trim() ? v.trim() : null);

export function toCreateInput(v: StaffFormValues): CreateStaffInput {
  return { login_id: (v.login_id ?? "").trim(), name: (v.name ?? "").trim(), email: blankToNull(v.email), roles: v.roles ?? [] };
}

/** 編集: 変わった項目だけを送る（何も変わっていなければ null） */
export function toUpdateInput(staff: Staff, v: StaffFormValues): UpdateStaffInput | null {
  const input: UpdateStaffInput = {};
  const name = (v.name ?? "").trim();
  const email = blankToNull(v.email);
  if (name !== staff.name) input.name = name;
  if (email !== staff.email) input.email = email;
  return Object.keys(input).length ? input : null;
}

/** ロールが変わったか（並び順は問わない） */
export function rolesChanged(staff: Staff, roles: RoleCode[]): boolean {
  return staff.roles.length !== roles.length || staff.roles.some((r) => !roles.includes(r));
}

/** 状態を変える操作（バックエンドと同じ遷移: 利用開始前 → 有効 ⇄ 利用停止中。有効化した後は利用開始前に戻せない） */
export interface StatusAction {
  label: string;
  to: StaffStatus;
  danger: boolean;
  confirm: string;
}

export function statusActions(staff: Staff): StatusAction[] {
  switch (staff.status) {
    case "pending":
      return [{ label: "有効化", to: "active", danger: false, confirm: "この職員を有効にします。持っているロールの操作ができるようになります。" }];
    case "active":
      return [{ label: "利用停止", to: "suspended", danger: true, confirm: "この職員のすべての操作を止めます。ロールはそのまま残り、再開すると元の権限で使えます。" }];
    case "suspended":
      return [{ label: "再開", to: "active", danger: false, confirm: "この職員を再び有効にします（停止前のロールで使えます）。" }];
  }
}

/** 自分の状態は変えられない・自分のシステム管理者ロールは外せない（バックエンドも拒否する） */
export const isSelf = (staff: Staff, me: Staff | null | undefined) => me?.id === staff.id;
export const lockedRoles = (staff: Staff | null, me: Staff | null | undefined): RoleCode[] =>
  staff && isSelf(staff, me) && staff.roles.includes("system_admin") ? ["system_admin"] : [];
