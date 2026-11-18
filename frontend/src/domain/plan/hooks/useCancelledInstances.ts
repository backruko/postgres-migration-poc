import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { addMonths, dayjs } from "../../../shared/lib/datetime";
import { instanceApi } from "../api";

/** 表示中の月（指定タイムゾーンの月初〜翌月初）に取り消された予定の一覧 */
export function useCancelledInstances(clientCode: string, month: string, timezone: string) {
  return useApiQuery(
    (signal) => {
      const start = dayjs.tz(`${month}-01T00:00:00`, timezone).format();
      const end = dayjs.tz(`${addMonths(month, 1)}-01T00:00:00`, timezone).format();
      return instanceApi.listCancelled(clientCode, start, end, signal);
    },
    [clientCode, month, timezone],
    { enabled: Boolean(clientCode), keepDataOnError: (e) => e.status !== 404 },
  );
}
