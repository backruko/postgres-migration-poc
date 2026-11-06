import { useSession } from "../../../shared/hooks/useSession";
import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { slaApi } from "../api";

/** SLA の集計。期間・顧客・ログインが変わると取り直す */
export function useSlaReport(from: string, to: string, clientCode: string | null) {
  const { version } = useSession();
  return useApiQuery(
    (signal) => slaApi.get({ from, to, clientCode: clientCode ?? undefined }, signal),
    [from, to, clientCode, version],
    { keepDataOnError: () => false },
  );
}
