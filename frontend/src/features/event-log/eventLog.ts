// 出来事の一覧画面の純粋な処理（種類の表示・期間・処理の状態・中身の読み取り）。UI から独立している。
import type { DomainEvent } from "../../domain/events/types";
import { ORDER_STATUS_LABEL } from "../../domain/ope/lib/orderActions";
import { dayjs } from "../../shared/lib/datetime";

/** 画面で選べる種類（バックエンドの formats.EventType と同じ名前） */
export const EVENT_TYPES: { value: string; label: string; color: string }[] = [
  { value: "OrderLate", label: "遅れ", color: "red" },
  { value: "OrderDueSoon", label: "まもなく", color: "orange" },
  { value: "DestinationTestRequested", label: "テスト送信", color: "blue" },  // 宛先のテスト送信
];

export const eventTypeInfo = (type: string) =>
  EVENT_TYPES.find((t) => t.value === type) ?? { value: type, label: type, color: "default" };

const DAY = /^\d{4}-\d{2}-\d{2}$/;
export const isDay = (v: string | null | undefined): v is string => !!v && DAY.test(v) && dayjs(v, "YYYY-MM-DD", true).isValid();

/**
 * 画面の期間（日付。両端を含む）を API の from / to（from 以上・to 未満）にする。
 * 日付は tz の暦日として読む: 2026-11-12〜2026-11-12 → 2026-11-12T00:00+09:00 以上、2026-11-13T00:00+09:00 未満
 */
export function periodQuery(fromDay: string | null, toDay: string | null, tz: string): { from?: string; to?: string } {
  return {
    from: isDay(fromDay) ? dayjs.tz(`${fromDay}T00:00:00`, tz).format() : undefined,
    to: isDay(toDay) ? dayjs.tz(`${toDay}T00:00:00`, tz).add(1, "day").format() : undefined,
  };
}

/** 起きてから記録されるまでの分（監視は毎分なので、ふつうは 0〜1。ワーカーが止まっていた分だけ大きくなる） */
export const recordDelayMinutes = (e: Pick<DomainEvent, "occurred_at" | "recorded_at">) =>
  Math.max(0, Math.floor(dayjs(e.recorded_at).diff(dayjs(e.occurred_at), "second") / 60));

export type Processing = { kind: "pending" | "done" | "failing"; label: string };

/** 通知の仕組みの処理の状態（振り分けが済むと通知の仕組みが書く） */
export function processingOf(e: Pick<DomainEvent, "processed_at" | "attempts" | "last_error">, tz: string): Processing {
  if (e.processed_at) return { kind: "done", label: `処理済み ${dayjs(e.processed_at).tz(tz).format("M/D HH:mm")}` };
  if (e.attempts > 0) return { kind: "failing", label: `失敗 ${e.attempts} 回` };
  return { kind: "pending", label: "未処理" };
}

/** 中身（版 1 のオーダーの写し）から表に出す項目。知らない形でも壊れないよう、文字列だけ拾う */
export type OrderSnapshot = {
  clientName: string;
  bizName: string;
  templateName: string;
  occurrenceDate: string;
  deadlineAt: string;
  status: string;
};

const text = (v: unknown) => (typeof v === "string" ? v : "");

export function orderSnapshot(payload: DomainEvent["payload"]): OrderSnapshot {
  return {
    clientName: text(payload.client_name),
    bizName: text(payload.biz_name),
    templateName: text(payload.template_name),
    occurrenceDate: text(payload.occurrence_date),
    deadlineAt: text(payload.deadline_at),
    status: text(payload.status),
  };
}

/** 見つけた時点のオーダーの状態の表示（オーダーの画面と同じ言葉。知らない値はそのまま） */
export const orderStatusLabel = (status: string) => (ORDER_STATUS_LABEL as Record<string, string>)[status] ?? status;
