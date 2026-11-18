// 届かなかったアラームを「確認した」ことを、画面どうしに知らせる小さな仕組み（メニューの件数・日次ダッシュボードの帯が、
// 押した直後に読み直せるように。2 分ごとの読み直しを待たない）。React に依存しない（templateChangeEvents と同じ形）。

let version = 0;
const listeners = new Set<() => void>();

/** 「確認した」が成功したときに呼ぶ（notificationApi が呼ぶ） */
export function notifyFailuresChanged(): void {
  version += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeFailures(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function failuresVersion(): number {
  return version;
}
