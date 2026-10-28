import { useActingStaffId } from "../../../shared/hooks/useActingStaffId";
import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { staffApi } from "../api";

/** 操作している職員。職員が選ばれていない間は取得しない。切り替えると取り直す */
export function useCurrentStaff() {
  const actingId = useActingStaffId();
  return useApiQuery((signal) => staffApi.me(signal), [actingId], {
    enabled: actingId !== null,
    keepDataOnError: () => false,
  });
}

/** 職員の一覧（参照できるロールが無い場合は 403） */
export function useStaffList(enabled: boolean) {
  const actingId = useActingStaffId();
  return useApiQuery((signal) => staffApi.list(signal), [actingId], { enabled, keepDataOnError: () => false });
}

/** ロールの一覧（有効な職員なら誰でも取得できる） */
export function useRoles(enabled: boolean) {
  const actingId = useActingStaffId();
  return useApiQuery((signal) => staffApi.listRoles(signal), [actingId], { enabled });
}
