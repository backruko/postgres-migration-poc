import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { templateApi } from "../api";

/** 業務のテンプレート一覧。業務IDが未指定の間は取得しない。存在しない業務（404）の場合は表示を消す */
export function usePlanTemplates(bizId: number | null) {
  return useApiQuery((signal) => templateApi.list(bizId as number, signal), [bizId], {
    enabled: bizId !== null,
    keepDataOnError: (e) => e.status !== 404,
  });
}
