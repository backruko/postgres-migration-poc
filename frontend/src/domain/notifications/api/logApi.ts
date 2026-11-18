import { http, unwrap } from "../../../shared/api/http";
import type { LogEntryDetail, LogFilter, NotificationLog } from "../types";

/** 通知の記録（N.5b の API）。有効な職員なら誰でも（担当顧客が限られた職員には、その顧客のものだけ） */
export const notificationLogApi = {
  /** 新しい順。event_id で絞ると、その出来事から作った通知（出来事 → 通知のつながり） */
  list(filter: LogFilter, signal?: AbortSignal): Promise<NotificationLog> {
    return unwrap(http.GET("/notifications/log", {
      params: {
        query: {
          status: filter.status, kind: filter.kind, client_code: filter.clientCode, rule_id: filter.ruleId,
          event_id: filter.eventId, order_id: filter.orderId, from: filter.from, to: filter.to, limit: filter.limit,
        },
      },
      signal,
    }));
  },
  /** 1 件（本文・画面へのリンクも） */
  get(id: number, signal?: AbortSignal): Promise<LogEntryDetail> {
    return unwrap(http.GET("/notifications/log/{notification_id}", { params: { path: { notification_id: id } }, signal }));
  },
};
