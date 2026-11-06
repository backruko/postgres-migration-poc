import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { clientApi } from "../api/clientApi";

/** 顧客の一覧（顧客の選択肢用。有効な顧客だけ） */
export function useClients() {
  return useApiQuery((signal) => clientApi.list(signal), []);
}

/** 無効にした顧客も含む一覧（顧客の設定画面用） */
export function useAllClients() {
  return useApiQuery((signal) => clientApi.list(signal, true), []);
}
