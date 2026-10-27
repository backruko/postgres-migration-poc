import { http, unwrap } from "../../../shared/api/http";
import type { PlanTemplate } from "../types";

/** 予定テンプレート（事前設定）。現時点では参照のみ */
export const templateApi = {
  /** 業務に属する有効なテンプレートの一覧（GET /plans/templates?biz_id=） */
  list(bizId: number, signal?: AbortSignal): Promise<PlanTemplate[]> {
    return unwrap(http.GET("/plans/templates", { params: { query: { biz_id: bizId } }, signal }));
  },
};
