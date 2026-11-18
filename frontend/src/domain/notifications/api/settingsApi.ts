import { http, unwrap } from "../../../shared/api/http";
import type {
  DestinationInput, NotificationDestination, NotificationOptions, NotificationRule, RuleInput,
} from "../types";

/**
 * 通知の設定（宛先・ルール。N.5a の API）。読むのは有効な職員なら誰でも（担当顧客が限られた職員には、その顧客と全顧客のもの）。
 * 変えるのは業務管理者だけ（限られた業務管理者は自分の顧客のものだけ。全顧客のものは 403）。
 */
export const notificationSettingsApi = {
  options(signal?: AbortSignal): Promise<NotificationOptions> {
    return unwrap(http.GET("/notifications/options", { signal }));
  },
  destinations(includeDeleted: boolean, signal?: AbortSignal): Promise<NotificationDestination[]> {
    return unwrap(http.GET("/notifications/destinations", { params: { query: { include_deleted: includeDeleted } }, signal }));
  },
  createDestination(body: DestinationInput): Promise<NotificationDestination> {
    return unwrap(http.POST("/notifications/destinations", { body }));
  },
  updateDestination(id: number, body: DestinationInput): Promise<NotificationDestination> {
    return unwrap(http.PUT("/notifications/destinations/{destination_id}", { params: { path: { destination_id: id } }, body }));
  },
  deleteDestination(id: number): Promise<NotificationDestination> {
    return unwrap(http.DELETE("/notifications/destinations/{destination_id}", { params: { path: { destination_id: id } } }));
  },
  restoreDestination(id: number): Promise<NotificationDestination> {
    return unwrap(http.POST("/notifications/destinations/{destination_id}/restore", { params: { path: { destination_id: id } } }));
  },
  /** テスト送信を頼む（ワーカーが 1 分以内に 1 回だけ送る。結果は一覧の last_test） */
  testDestination(id: number): Promise<{ event_id: number }> {
    return unwrap(http.POST("/notifications/destinations/{destination_id}/test", { params: { path: { destination_id: id } } }));
  },
  rules(includeDeleted: boolean, signal?: AbortSignal): Promise<NotificationRule[]> {
    return unwrap(http.GET("/notifications/rules", { params: { query: { include_deleted: includeDeleted } }, signal }));
  },
  createRule(body: RuleInput): Promise<NotificationRule> {
    return unwrap(http.POST("/notifications/rules", { body }));
  },
  updateRule(id: number, body: RuleInput): Promise<NotificationRule> {
    return unwrap(http.PUT("/notifications/rules/{rule_id}", { params: { path: { rule_id: id } }, body }));
  },
  deleteRule(id: number): Promise<NotificationRule> {
    return unwrap(http.DELETE("/notifications/rules/{rule_id}", { params: { path: { rule_id: id } } }));
  },
  restoreRule(id: number): Promise<NotificationRule> {
    return unwrap(http.POST("/notifications/rules/{rule_id}/restore", { params: { path: { rule_id: id } } }));
  },
};
