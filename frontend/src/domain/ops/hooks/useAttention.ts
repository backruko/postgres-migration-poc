import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { dashboardApi } from "../api";

/** 締切が近い・遅れのオーダー（日付によらず。日次ダッシュボードの上の一覧） */
export function useAttention() {
  return useApiQuery((signal) => dashboardApi.getAttention(200, signal), []);
}
