import { App } from "antd";
import { useState } from "react";
import { ApiError } from "../../shared/api/http";

/**
 * 更新系 API の送信状態。成功時はメッセージを出して onDone を呼び、失敗時は API のエラーメッセージを表示する。
 * 失敗してもモーダルは閉じない（入力し直せるように）。
 */
export function useSubmit(onDone: () => void) {
  const { message } = App.useApp();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (action: () => Promise<unknown>, successMessage: string) => {
    setSubmitting(true);
    try {
      await action();
      message.success(successMessage);
      onDone();
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return { submitting, submit };
}
