import { useSyncExternalStore } from "react";
import { getActingStaffId, subscribeActingStaff } from "../api/actingStaff";

/** 操作している職員の ID（ナビで切り替えると再描画される） */
export function useActingStaffId(): number | null {
  return useSyncExternalStore(subscribeActingStaff, getActingStaffId);
}
