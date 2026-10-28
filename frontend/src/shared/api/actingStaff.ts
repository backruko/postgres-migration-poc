import { DEFAULT_STAFF_ID } from "../config";

/**
 * 操作している職員の ID（ログインが入るまでの開発用）。
 * すべての API リクエストに X-Staff-Id ヘッダーとして付ける（http.ts）。バックエンドはこのヘッダーで職員とロールを判定する。
 * ナビで選んだ ID をブラウザに記憶する（未選択なら VITE_DEFAULT_STAFF_ID）。
 * ログインを入れるときは、この仕組みをセッション・トークンに置き換える。
 */
const STORAGE_KEY = "plan-calendar:acting-staff-id";

function load(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const id = raw === null ? NaN : Number(raw);
    return Number.isInteger(id) && id > 0 ? id : DEFAULT_STAFF_ID;
  } catch {
    return DEFAULT_STAFF_ID;
  }
}

let current = load();
const listeners = new Set<() => void>();

export function getActingStaffId(): number | null {
  return current;
}

export function setActingStaffId(id: number | null) {
  current = id;
  try {
    if (id === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    // ストレージが使えない環境では、このタブの間だけ有効
  }
  listeners.forEach((l) => l());
}

/** useSyncExternalStore 用 */
export function subscribeActingStaff(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
