import { http, unwrap } from "../../../shared/api/http";
import type { LoginInput, PasswordChangeInput, Staff } from "../types";

/** ログイン・ログアウト・本人のパスワードの変更（LG.3）。セッションは HttpOnly の Cookie で、画面からは読めない */
export const authApi = {
  /** ログイン（Cookie が設定される）（POST /auth/login） */
  login(input: LoginInput): Promise<Staff> {
    return unwrap(http.POST("/auth/login", { body: input }));
  },

  /** ログアウト（ログインしていなくても成功）（POST /auth/logout） */
  async logout(): Promise<void> {
    await http.POST("/auth/logout");
  },

  /** 本人のパスワードの変更（仮パスワードからも。このセッションは続く）（POST /auth/password） */
  changePassword(input: PasswordChangeInput): Promise<Staff> {
    return unwrap(http.POST("/auth/password", { body: input }));
  },
};
