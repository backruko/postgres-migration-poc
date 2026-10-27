import { http, unwrap } from "../../../shared/api/http";
import type { CreatePlanTemplateInput, PlanTemplate } from "../types";

/** 予定テンプレート（事前設定）。現時点では参照と作成のみ */
export const templateApi = {
  /** 業務に属する有効なテンプレートの一覧（GET /plans/templates?biz_id=） */
  list(bizId: number, signal?: AbortSignal): Promise<PlanTemplate[]> {
    return unwrap(http.GET("/plans/templates", { params: { query: { biz_id: bizId } }, signal }));
  },

  /**
   * テンプレートを作成する（POST /plans/templates）。
   * 作成と同時に、今から90日先までの予定がバックエンドで生成される。
   */
  create(input: CreatePlanTemplateInput): Promise<PlanTemplate> {
    return unwrap(http.POST("/plans/templates", { body: input }));
  },
};
