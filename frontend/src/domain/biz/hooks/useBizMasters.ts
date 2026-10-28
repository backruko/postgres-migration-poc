import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { bizApi } from "../api/bizApi";

/** 業務の一覧（業務の選択肢用） */
export function useBizMasters() {
  return useApiQuery((signal) => bizApi.list(signal), []);
}
