import { ApiError } from "../../../shared/api/http";

/**
 * ログイン・パスワードの画面の表示（バックエンドの決まり: src/domain_staff/credentials.py）。
 * バックエンドのメッセージは英語なので、エラーコードで日本語にする。
 */
export const PASSWORD_MIN_LENGTH = 8;

/** ログインの失敗の説明 */
export function loginErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "通信に失敗しました。もう一度お試しください。";
  switch (e.code) {
    case "INVALID_CREDENTIALS":
      return "ログインIDまたはパスワードが違います。";
    case "ACCOUNT_LOCKED": {
      const until = /after (\d{1,2}:\d{2})/.exec(e.message)?.[1];
      return `ログインの失敗が続いたため、${until ? `${until} まで` : "しばらく"}ログインできません。急ぐときはシステム管理者に仮パスワードを設定してもらってください。`;
    }
    case "STAFF_NOT_ACTIVE":
      return "このアカウントは利用開始前か利用停止中です。システム管理者に連絡してください。";
    default:
      return e.message;
  }
}

/** パスワードの変更の失敗の説明 */
export function passwordChangeErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "通信に失敗しました。もう一度お試しください。";
  if (e.code === "INVALID_CREDENTIALS") return "今のパスワードが違います。";
  if (/different from the current/.test(e.message)) return "今と違うパスワードにしてください。";
  if (/at least/.test(e.message)) return `パスワードは ${PASSWORD_MIN_LENGTH} 文字以上にしてください。`;
  if (/login ID/.test(e.message)) return "ログインIDと同じパスワードは使えません。";
  if (/space/.test(e.message)) return "パスワードの先頭・末尾に空白は使えません。";
  return e.message;
}

/** 新しいパスワードの入力の誤り（なければ null。最終的な確認はバックエンド） */
export function newPasswordProblem(password: string, loginId: string | undefined): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) return `${PASSWORD_MIN_LENGTH} 文字以上にしてください`;
  if (password.length > 128) return "128 文字までです";
  if (password.trim() !== password) return "先頭・末尾に空白は使えません";
  if (loginId && password.toLowerCase() === loginId.toLowerCase()) return "ログインIDと同じパスワードは使えません";
  return null;
}
