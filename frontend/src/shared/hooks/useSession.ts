import { useSyncExternalStore } from "react";
import { getSessionState, subscribeSession } from "../api/session";

/** ログインの状態（ログイン・ログアウト・401 で再描画される） */
export function useSession() {
  return useSyncExternalStore(subscribeSession, getSessionState);
}
