import { http, unwrap } from "../../../shared/api/http";
import type { DomainEventList, EventFilter } from "../types";

export const eventApi = {
  /**
   * 記録された出来事の一覧（GET /events/。読み取りのみ）: 起きた日時の新しい順。
   * ログインした職員なら誰でも。担当顧客が限られた職員には、その顧客の出来事だけが返る。
   */
  list(filter: EventFilter, signal?: AbortSignal): Promise<DomainEventList> {
    return unwrap(
      http.GET("/events/", {
        params: {
          query: {
            event_type: filter.eventType,
            client_code: filter.clientCode,
            order_id: filter.orderId,
            from: filter.from,
            to: filter.to,
            limit: filter.limit,
          },
        },
        signal,
      }),
    );
  },
};
