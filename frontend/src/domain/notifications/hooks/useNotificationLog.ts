import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useSession } from "../../../shared/hooks/useSession";
import { notificationLogApi } from "../api/logApi";
import type { LogFilter } from "../types";

/** 通知の記録（絞り込み・ログインが変わると取り直す。自動では読み直さない） */
export function useNotificationLog(filter: LogFilter, enabled = true) {
  const { version } = useSession();
  return useApiQuery(
    (signal) => notificationLogApi.list(filter, signal),
    [filter.status, filter.kind, filter.clientCode, filter.ruleId, filter.eventId, filter.orderId, filter.from, filter.to, filter.limit, version],
    { enabled },
  );
}

/** 通知 1 件（本文も）。行を開いたときだけ読む */
export function useNotificationLogEntry(id: number, enabled: boolean) {
  const { version } = useSession();
  return useApiQuery((signal) => notificationLogApi.get(id, signal), [id, version], { enabled });
}
