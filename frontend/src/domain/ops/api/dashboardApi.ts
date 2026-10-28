import { http, unwrap } from "../../../shared/api/http";
import type { DailyDashboard } from "../types";

export const dashboardApi = {
  /**
   * 指定日の全顧客の予定・シート日付・休日（GET /ops/daily）。
   * date を省略するとタイムゾーンでの今日。過去の日付では無効化・取消された予定も理由付きで返る。
   */
  getDaily(date: string | null, options: { timezone?: string } = {}, signal?: AbortSignal): Promise<DailyDashboard> {
    return unwrap(
      http.GET("/ops/daily", {
        params: { query: { date: date || undefined, timezone: options.timezone } },
        signal,
      }),
    );
  },
};
