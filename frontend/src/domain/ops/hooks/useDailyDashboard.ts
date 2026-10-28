import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { dashboardApi } from "../api";

/** 指定日の日次ダッシュボード（全顧客横断） */
export function useDailyDashboard(date: string, timezone: string) {
  return useApiQuery((signal) => dashboardApi.getDaily(date, { timezone }, signal), [date, timezone]);
}
