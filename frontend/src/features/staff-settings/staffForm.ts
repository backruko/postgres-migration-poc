import { MACHINE_ROLES } from "../../domain/staff/lib/permissions";
import type { CreateStaffInput, RoleCode, Staff, StaffStatus, UpdateStaffInput } from "../../domain/staff/types";

/** フォームの値（登録・編集で共通） */
export interface StaffFormValues {
  login_id?: string;
  name?: string;
  email?: string;
  roles?: RoleCode[];
  /** 担当する顧客（空 = 全顧客） */
  client_codes?: string[];
}

/**
 * 選べないロール（機械用のアカウントの決まり。バックエンドも 400 にする）:
 * システム連携（system_agent）を選んでいれば人のロールは選べず、人のロールを選んでいればシステム連携は選べない
 */
export function machineOnlyDisabled(selected: RoleCode[], all: RoleCode[]): RoleCode[] {
  if (selected.includes("system_agent")) return all.filter((r) => !MACHINE_ROLES.includes(r));
  if (selected.some((r) => !MACHINE_ROLES.includes(r))) return ["system_agent"];
  return [];
}

/** 担当顧客が変わったか（並び順は問わない） */
export function clientsChanged(current: string[], next: string[]): boolean {
  return current.length !== next.length || current.some((c) => !next.includes(c));
}

/**
 * ロールと担当顧客を保存する順番。システム管理者は担当顧客を持てないので（バックエンドが 400 にする）、
 * 担当顧客を外すときは先に担当顧客を、付けるときは先にロールを保存する
 */
export function saveOrder(nextClients: string[]): "clients-first" | "roles-first" {
  return nextClients.length === 0 ? "clients-first" : "roles-first";
}

/** 担当顧客の選択肢: 有効な顧客と、今の担当に残っている（無効にした）顧客 */
export function clientOptions(active: { code: string; name: string }[], current: string[]) {
  const known = new Set(active.map((c) => c.code));
  return [
    ...active.map((c) => ({ value: c.code, label: `${c.code} ${c.name}` })),
    ...current.filter((c) => !known.has(c)).map((c) => ({ value: c, label: `${c}（無効）` })),
  ];
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
