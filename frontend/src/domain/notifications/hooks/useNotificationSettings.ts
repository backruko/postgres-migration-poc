import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useSession } from "../../../shared/hooks/useSession";
import { notificationSettingsApi } from "../api/settingsApi";

/** ルールの画面で選べる出来事の種類・文面 */
export function useNotificationOptions() {
  const { version } = useSession();
  return useApiQuery((signal) => notificationSettingsApi.options(signal), [version]);
}

/** 宛先の一覧（includeDeleted: 削除したものも） */
export function useNotificationDestinations(includeDeleted: boolean) {
  const { version } = useSession();
  return useApiQuery((signal) => notificationSettingsApi.destinations(includeDeleted, signal), [includeDeleted, version]);
}

/** ルールの一覧（includeDeleted: 削除したものも） */
export function useNotificationRules(includeDeleted: boolean) {
  const { version } = useSession();
  return useApiQuery((signal) => notificationSettingsApi.rules(includeDeleted, signal), [includeDeleted, version]);
}
