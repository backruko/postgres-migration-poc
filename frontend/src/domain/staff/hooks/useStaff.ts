import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useSession } from "../../../shared/hooks/useSession";
import { staffApi } from "../api";

/** ログインしている職員（ログイン・ログアウトのたびに取り直す。未ログインなら 401） */
export function useCurrentStaff() {
  const { version } = useSession();
  return useApiQuery((signal) => staffApi.me(signal), [version], { keepDataOnError: () => false });
}

/** 職員の一覧（参照できるロールが無い場合は 403） */
export function useStaffList(enabled: boolean) {
  const { version } = useSession();
  return useApiQuery((signal) => staffApi.list(signal), [version], { enabled, keepDataOnError: () => false });
}

/** ロールの一覧（有効な職員なら誰でも取得できる） */
export function useRoles(enabled: boolean) {
  const { version } = useSession();
  return useApiQuery((signal) => staffApi.listRoles(signal), [version], { enabled });
}
