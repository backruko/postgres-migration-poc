import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useAutoRefresh } from "../../../shared/hooks/useAutoRefresh";
import { useSession } from "../../../shared/hooks/useSession";
import { workerApi } from "../api";

/** ワーカーの状態を読み直す間隔（心拍は毎分。止まってから 5 分で「止まっている」になるので、1 分ごとで十分） */
const REFRESH_MS = 60 * 1000;

/** ワーカーが動いているか（ナビゲーションの印と日次ダッシュボードの赤い帯）。1 分ごとに読み直す */
export function useWorkerHealth(enabled: boolean) {
  const { version } = useSession();
  const query = useApiQuery((signal) => workerApi.health(signal), [version], { enabled });
  useAutoRefresh(() => enabled && query.reload(), REFRESH_MS);
  return query;
}

/** 期間のまとめと受け止めたエラー（「ワーカーの状態」の画面）。期間・ログインが変わると取り直し、1 分ごとに読み直す */
export function useWorkerHealthReport(from: string, to: string, enabled: boolean) {
  const { version } = useSession();
  const summary = useApiQuery((signal) => workerApi.summary({ from, to }, signal), [from, to, version], {
    enabled, keepDataOnError: () => false,
  });
  const errors = useApiQuery((signal) => workerApi.errors({ from, to }, signal), [from, to, version], {
    enabled, keepDataOnError: () => false,
  });
  useAutoRefresh(() => {
    if (!enabled) return;
    summary.reload();
    errors.reload();
  }, REFRESH_MS);
  return { summary, errors };
}
