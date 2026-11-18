/**
 * ログインの状態（LG.3）。セッション自体は HttpOnly の Cookie（ops_session）にあり、画面からは読めない。
 * ここで持つのは「ログイン・ログアウトのたびに増える番号」だけで、職員・一覧を取り直すきっかけに使う。
 * API が 401 を返したら（期限切れ・ログアウト・停止）signedOut になり、レイアウトがログイン画面へ移す。
 */
type SessionState = { version: number; signedOut: boolean };

let state: SessionState = { version: 0, signedOut: false };
const listeners = new Set<() => void>();

const set = (next: SessionState) => {
  state = next;
  listeners.forEach((l) => l());
};

export const getSessionState = () => state;

export function subscribeSession(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** ログインした（職員・一覧を取り直す） */
export const markSignedIn = () => set({ version: state.version + 1, signedOut: false });

/** ログアウトした・セッションが切れた（API の 401） */
export const markSignedOut = () => {
  if (!state.signedOut) set({ version: state.version + 1, signedOut: true });
};
