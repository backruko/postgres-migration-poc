import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { clientApi } from "../api/clientApi";

/** 顧客の一覧（顧客の選択肢用） */
export function useClients() {
  return useApiQuery((signal) => clientApi.list(signal), []);
}
