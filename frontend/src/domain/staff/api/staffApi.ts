import { http, unwrap } from "../../../shared/api/http";
import type { CreateStaffInput, Role, RoleCode, Staff, StaffStatus, UpdateStaffInput } from "../types";

/**
 * 職員・ロール。どのリクエストにも、操作している職員（X-Staff-Id）が http.ts で付く。
 * 参照はシステム管理者・業務管理者・監査担当、登録・変更はシステム管理者のみ（バックエンドが判定する）。
 */
export const staffApi = {
  /** 操作している職員（利用開始前・停止中でも返る）（GET /staff/me） */
  me(signal?: AbortSignal): Promise<Staff> {
    return unwrap(http.GET("/staff/me", { signal }));
  },

  /** ロールの一覧（GET /staff/roles） */
  listRoles(signal?: AbortSignal): Promise<Role[]> {
    return unwrap(http.GET("/staff/roles", { signal }));
  },

  /** 職員の一覧（ログインID順）（GET /staff/） */
  list(signal?: AbortSignal): Promise<Staff[]> {
    return unwrap(http.GET("/staff/", { signal }));
  },

  /** 職員を登録する（利用開始前の状態で作られる）（POST /staff/） */
  create(input: CreateStaffInput): Promise<Staff> {
    return unwrap(http.POST("/staff/", { body: input }));
  },

  /** 氏名・メールアドレスを変更する（PATCH /staff/{id}） */
  update(staffId: number, input: UpdateStaffInput): Promise<Staff> {
    return unwrap(http.PATCH("/staff/{staff_id}", { params: { path: { staff_id: staffId } }, body: input }));
  },

  /** ロールを指定した集合に置き換える（自分のシステム管理者ロールは外せない）（PUT /staff/{id}/roles） */
  setRoles(staffId: number, roles: RoleCode[]): Promise<Staff> {
    return unwrap(http.PUT("/staff/{staff_id}/roles", { params: { path: { staff_id: staffId } }, body: { roles } }));
  },

  /** 有効化・利用停止・再開（ロールはそのまま。自分の状態は変えられない）（PUT /staff/{id}/status） */
  setStatus(staffId: number, status: StaffStatus): Promise<Staff> {
    return unwrap(http.PUT("/staff/{staff_id}/status", { params: { path: { staff_id: staffId } }, body: { status } }));
  },
};
