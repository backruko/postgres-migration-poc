import { useSyncExternalStore } from "react";
import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useAutoRefresh } from "../../../shared/hooks/useAutoRefresh";
import { useSession } from "../../../shared/hooks/useSession";
import { notificationApi } from "../api";
import { failuresVersion, subscribeFailures } from "../lib/failureEvents";

/** 読み直す間隔（日次ダッシュボード・承認待ちの件数と同じ 2 分） */
const REFRESH_MS = 2 * 60 * 1000;

/**
 * 届かなかったアラームと再試行中の数（メニューの赤い件数・日次ダッシュボードの帯とタブ）。
 * 2 分ごとと、「確認した」の直後に読み直す。enabled=false（有効でない職員: API が 403）の間は読まない
 */
export function useNotificationFailures(enabled: boolean) {
  const { version: session } = useSession();
  const version = useSyncExternalStore(subscribeFailures, failuresVersion);
  const query = useApiQuery((signal) => notificationApi.failures(signal), [version, session], { enabled });
  useAutoRefresh(() => enabled && query.reload(), REFRESH_MS);
  return query;
}
