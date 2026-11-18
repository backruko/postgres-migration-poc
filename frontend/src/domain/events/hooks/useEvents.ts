import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useSession } from "../../../shared/hooks/useSession";
import { eventApi } from "../api";
import type { EventFilter } from "../types";

/** 出来事の一覧。絞り込み・ログインが変わると取り直す（自動では読み直さない: 「再読み込み」で） */
export function useEvents(filter: EventFilter) {
  const { version } = useSession();
  return useApiQuery(
    (signal) => eventApi.list(filter, signal),
    [filter.eventType, filter.clientCode, filter.orderId, filter.from, filter.to, filter.limit, version],
    { keepDataOnError: () => false },
  );
}
