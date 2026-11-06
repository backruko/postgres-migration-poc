import { http, unwrap } from "../../../shared/api/http";
import type { SlaReport } from "../types";

export const slaApi = {
  /**
   * SLA の集計（GET /ops/sla）: 顧客 × 締切の月（日本時間）。from / to は YYYY-MM（省略時は今月までの 12 か月）。
   * 有効な職員なら誰でも（ログインしていなければ 401）。
   */
  get(params: { from?: string; to?: string; clientCode?: string }, signal?: AbortSignal): Promise<SlaReport> {
    return unwrap(
      http.GET("/ops/sla", {
        params: { query: { from: params.from, to: params.to, client_code: params.clientCode } },
        signal,
      }),
    );
  },
};
