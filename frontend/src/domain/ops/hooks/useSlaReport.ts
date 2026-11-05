import { useActingStaffId } from "../../../shared/hooks/useActingStaffId";
import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { slaApi } from "../api";

/** SLA の集計。期間・顧客・操作する職員が変わると取り直す */
export function useSlaReport(from: string, to: string, clientCode: string | null) {
  const actingId = useActingStaffId();
  return useApiQuery(
    (signal) => slaApi.get({ from, to, clientCode: clientCode ?? undefined }, signal),
    [from, to, clientCode, actingId],
    { keepDataOnError: () => false },
  );
}
