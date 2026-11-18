import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { signalApi } from "../api/signalApi";

/** 人の対応が必要な信号。enabled が false の間は取得しない（ログインしている職員が有効でないとき） */
export function useOpenSignals(enabled: boolean) {
  return useApiQuery((signal) => signalApi.listOpen(signal), [enabled], { enabled });
}
