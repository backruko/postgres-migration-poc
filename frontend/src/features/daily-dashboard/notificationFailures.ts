// 日次ダッシュボードの「届かなかったアラーム」の純粋な処理（帯の文・状態の言葉・確認の前後の分け方）。UI から独立している。
import type { FailedDelivery, FailedNotification, NotificationFailures } from "../../domain/notifications/types";
import { dayjs } from "../../shared/lib/datetime";

/** 通知の状態（届かなかったものだけが来る） */
export const NOTIFICATION_STATUS_LABEL: Record<string, string> = {
  failed: "届かなかった",
  partly_failed: "一部に届かなかった",
};

/** 宛先ごとの結果 */
export const DELIVERY_STATUS_LABEL: Record<FailedDelivery["status"], string> = {
  pending: "待ち",
  retrying: "再試行中",
  sent: "届いた",
  failed: "届かなかった",
};

export const DELIVERY_STATUS_COLOR: Record<FailedDelivery["status"], string | undefined> = {
  pending: undefined,
  retrying: "gold",
  sent: "green",
  failed: "red",
};

/** 赤い帯の文（まだ確認されていないものがなければ null） */
export const failureBannerText = (data: NotificationFailures | null | undefined) =>
  data && data.unacknowledged > 0 ? `届かなかったアラームが ${data.unacknowledged} 件あります。確認してください。` : null;

/** 黄色の行の文（いま再試行中の配信がなければ null）。メールサーバーの不調の早い知らせ */
export function retryingText(data: NotificationFailures | null | undefined, tz: string): string | null {
  if (!data || data.retrying === 0) return null;
  const last = data.last_retry_at ? `（最後の失敗 ${dayjs(data.last_retry_at).tz(tz).format("HH:mm")}${data.last_retry_error ? `: ${data.last_retry_error}` : ""}）` : "";
  return `メールの送信を再試行中: ${data.retrying} 件${last}`;
}

/** 一覧を「まだ確認していない」と「確認済み（直近 7 日）」に分ける（どちらも新しい順のまま） */
export function splitByConfirmation(items: FailedNotification[]): { open: FailedNotification[]; confirmed: FailedNotification[] } {
  return {
    open: items.filter((i) => !i.acknowledged_at),
    confirmed: items.filter((i) => !!i.acknowledged_at),
  };
}

/** 確認済みの行の文（例: 確認: 山田 11/12 14:30） */
export const confirmedText = (i: FailedNotification, tz: string) =>
  i.acknowledged_at ? `確認: ${i.acknowledged_by_name ?? "（削除された職員）"} ${dayjs(i.acknowledged_at).tz(tz).format("M/D HH:mm")}` : "";

/** 宛先 1 つの結果の文（例: ops@example.com 届かなかった（5 回）） */
export function deliveryText(d: FailedDelivery): string {
  const tries = d.status === "failed" || d.status === "retrying" ? `（${d.attempts} 回）` : "";
  return `${d.destination_name}（${d.address}）${DELIVERY_STATUS_LABEL[d.status]}${tries}`;
}
