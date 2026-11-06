import { http, unwrap } from "../../../shared/api/http";
import type { ApiKey, ApiKeyIssued, CreateStaffInput, Role, RoleCode, Staff, StaffStatus, UpdateStaffInput } from "../types";

/**
 * 職員・ロール。ログインのセッション（Cookie）で、ログインしている職員として呼ぶ。
 * 参照はシステム管理者・業務管理者・監査担当、登録・変更はシステム管理者のみ（バックエンドが判定する）。
 */
export const staffApi = {
  /** ログインしている職員（仮パスワードのままでも返る）（GET /staff/me） */
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

  /** 有効化・利用停止・再開（ロールはそのまま。自分の状態は変えられない。停止でセッションも終わる）（PUT /staff/{id}/status） */
  setStatus(staffId: number, status: StaffStatus): Promise<Staff> {
    return unwrap(http.PUT("/staff/{staff_id}/status", { params: { path: { staff_id: staffId } }, body: { status } }));
  },

  /** 仮パスワードを設定する（本人は次のログインで変える。ロックも解ける。自分には使えない）（PUT /staff/{id}/password） */
  setTemporaryPassword(staffId: number, password: string): Promise<Staff> {
    return unwrap(http.PUT("/staff/{staff_id}/password", { params: { path: { staff_id: staffId } }, body: { password } }));
  },

  /** API キーの一覧（取り消したものも。キーそのものは返らない）（GET /staff/{id}/api-keys） */
  listApiKeys(staffId: number, signal?: AbortSignal): Promise<ApiKey[]> {
    return unwrap(http.GET("/staff/{staff_id}/api-keys", { params: { path: { staff_id: staffId } }, signal }));
  },

  /** API キーを発行する（信号の送信の職員だけ。キーはこの応答でだけ見られる）（POST /staff/{id}/api-keys） */
  issueApiKey(staffId: number, label: string): Promise<ApiKeyIssued> {
    return unwrap(http.POST("/staff/{staff_id}/api-keys", { params: { path: { staff_id: staffId } }, body: { label } }));
  },

  /** API キーを取り消す（POST /staff/api-keys/{id}/revoke） */
  revokeApiKey(keyId: number): Promise<ApiKey> {
    return unwrap(http.POST("/staff/api-keys/{key_id}/revoke", { params: { path: { key_id: keyId } } }));
  },
};
