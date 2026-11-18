// テンプレートの変更依頼が変わったこと（作る・直す・取り下げる・承認・差し戻し）を、画面どうしに知らせる小さな仕組み。
// 承認待ちの件数のバッジ・承認待ちの一覧が、決めた直後に読み直せるように（2 分ごとの読み直しを待たない）。React に依存しない。

let version = 0;
const listeners = new Set<() => void>();

/** 変更依頼の API が成功したときに呼ぶ（templateChangeApi が呼ぶ） */
export function notifyTemplateChangesChanged(): void {
  version += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeTemplateChanges(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function templateChangesVersion(): number {
  return version;
}
