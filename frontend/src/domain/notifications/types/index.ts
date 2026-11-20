// 通知の仕組み（届かなかったアラーム）の型。OpenAPI から生成した型の別名で、手書きの型は持たない。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

export type NotificationFailures = Schemas["NotificationFailuresResponse"];
export type FailedNotification = Schemas["FailedNotification"];
export type FailedDelivery = Schemas["FailedDelivery"];

// ---- 通知の設定（宛先・ルール） ----
export type NotificationDestination = Schemas["DestinationOut"];
export type DestinationInput = Schemas["DestinationIn"];
export type NotificationRule = Schemas["RuleOut"];
export type RuleInput = Schemas["RuleIn"];
export type NotificationOptions = Schemas["NotificationOptions"];
export type LastTest = Schemas["LastTest"];
export type QuietMode = Schemas["QuietMode"];

// ---- 通知の記録 ----
export type NotificationLog = Schemas["NotificationLogResponse"];
export type LogEntry = Schemas["LogEntry"];
export type LogEntryDetail = Schemas["LogEntryDetail"];
export type LogDelivery = Schemas["LogDelivery"];
export type DeliveryAttempt = Schemas["AttemptOut"];
export type NotificationStatus = Schemas["NotificationStatus"];

/** 記録の絞り込み（from / to はオフセット付きの ISO 8601。from 以上・to 未満） */
export type LogFilter = {
  status?: NotificationStatus;
  kind?: "alarm" | "test";
  clientCode?: string;
  ruleId?: number;
  eventId?: number;
  orderId?: number;
  from?: string;
  to?: string;
  limit?: number;
};
