import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { sheetApi } from "../api";

/** 顧客の年間計画シート一覧（シート参照テンプレートの選択肢用）。顧客コードが未指定の間は取得しない */
export function useReferenceSheets(clientCode: string | null, enabled = true) {
  return useApiQuery((signal) => sheetApi.listByClient(clientCode as string, signal), [clientCode], {
    enabled: enabled && clientCode !== null,
  });
}
