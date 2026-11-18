import { http, unwrap } from "../../../shared/api/http";
import type { Attention, DailyDashboard } from "../types";

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

  /**
   * 完了・取消以外で、締切を過ぎた（遅れ）か「まもなく」の時刻を過ぎたオーダー（GET /ops/attention）。
   * 日付によらず締切の早い順（昨日から遅れているオーダーも出る）。
   */
  getAttention(limit = 200, signal?: AbortSignal): Promise<Attention> {
    return unwrap(http.GET("/ops/attention", { params: { query: { limit } }, signal }));
  },
};
