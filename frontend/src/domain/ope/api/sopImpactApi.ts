import { http, unwrap } from "../../../shared/api/http";
import type { SopImpact } from "../types";

export const sopImpactApi = {
  /**
   * effectiveDate（YYYY-MM-DD）の 0:00 日本時間から業務の版を切り替えると、どのオーダーが新しい版を使うか
   * （GET /ope/sop-impact。will_switch = 新しい版を使う、pinned = 指定した版のまま、released = リリースで固定した版のまま）
   */
  get(bizId: number, effectiveDate: string, limit = 20, signal?: AbortSignal): Promise<SopImpact> {
    return unwrap(http.GET("/ope/sop-impact", { params: { query: { biz_id: bizId, effective_date: effectiveDate, limit } }, signal }));
  },
};
