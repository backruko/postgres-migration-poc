// 出来事のページの「通知」タブ（通知の記録）の純粋な処理（状態・理由・試みの言葉）。UI から独立している。
import type { DeliveryAttempt, LogEntry, NotificationStatus } from "../../domain/notifications/types";

export const NOTIFICATION_STATUSES: { value: NotificationStatus; label: string; color: string | undefined }[] = [
  { value: "sent", label: "届いた", color: "green" },
  { value: "partly_failed", label: "一部に届かなかった", color: "orange" },
  { value: "failed", label: "届かなかった", color: "red" },
  { value: "pending", label: "送信中・待ち", color: "processing" },
  { value: "suppressed", label: "知らせなかった", color: "default" },
];

export const statusInfo = (s: NotificationStatus) =>
  NOTIFICATION_STATUSES.find((x) => x.value === s) ?? { value: s, label: s, color: undefined };

/** 知らせなかった理由（サーバーは英語のコード） */
export const SUPPRESSED_REASON_LABEL: Record<string, string> = {
  dedupe_window: "重複の期間内（同じオーダー・同じ種類をもう知らせた）",
  quiet_hours: "知らせない時間帯（知らせない設定）",
  no_destination: "使える宛先がない（無効・削除・別の顧客）",
  template_missing: "文面がない（設定の誤り）",
};

export const suppressedText = (reason: string | null | undefined) => (reason ? SUPPRESSED_REASON_LABEL[reason] ?? reason : null);

export const KIND_OPTIONS = [
  { value: "alarm" as const, label: "アラーム（ルール）" },
  { value: "test" as const, label: "テスト送信" },
];

/** ルールの列の文（テスト送信はルールなし） */
export const ruleText = (e: Pick<LogEntry, "is_test" | "rule_name">) => (e.is_test ? "テスト送信" : e.rule_name ?? "（削除されたルール）");

/** 1 回の試みの結果の文 */
export const attemptResultText = (a: DeliveryAttempt) =>
  a.ok ? "届いた" : a.permanent ? "届かなかった（もう試さない）" : "届かなかった（後で送り直す）";

/** 宛先ごとの短い文（一覧の 1 行用。例: 運用チーム 届いた / 当番 届かなかった（5 回）） */
export function deliverySummary(e: Pick<LogEntry, "deliveries">): string {
  const label: Record<string, string> = { sent: "届いた", failed: "届かなかった", retrying: "送り直し中", pending: "待ち" };
  return e.deliveries
    .map((d) => `${d.destination_name} ${label[d.status] ?? d.status}${d.status === "failed" || d.status === "retrying" ? `（${d.attempts} 回）` : ""}`)
    .join(" / ");
}

/** 送ったアドレスが、いまの宛先のアドレスと違う回があるか（アドレスを直した後の再試行） */
export const addressChanged = (d: { address: string; attempt_log: DeliveryAttempt[] }) => d.attempt_log.some((a) => a.address !== d.address);
