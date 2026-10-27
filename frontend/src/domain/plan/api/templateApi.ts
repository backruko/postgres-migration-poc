import { http, unwrap } from "../../../shared/api/http";
import type { CreatePlanTemplateInput, PlanTemplate, UpdatePlanTemplateInput } from "../types";

/** 予定テンプレート（事前設定）。現時点では参照・作成・編集のみ */
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

  /**
   * テンプレートを更新する（PATCH /plans/templates/{id}）。
   * 生成に関わる項目が実際に変わった場合だけ改訂番号が上がり、未来の予定が再生成される（手動変更は維持）。
   */
  update(templateId: number, input: UpdatePlanTemplateInput): Promise<PlanTemplate> {
    return unwrap(
      http.PATCH("/plans/templates/{template_id}", { params: { path: { template_id: templateId } }, body: input }),
    );
  },

  /** 指定日テンプレートに日付を追加する（POST /plans/templates/{id}/dates） */
  addDates(templateId: number, dates: string[]): Promise<PlanTemplate> {
    return unwrap(
      http.POST("/plans/templates/{template_id}/dates", { params: { path: { template_id: templateId } }, body: { dates } }),
    );
  },

  /** 指定日テンプレートから日付を1件削除する（DELETE /plans/templates/{id}/dates/{date}） */
  removeDate(templateId: number, localDate: string): Promise<PlanTemplate> {
    return unwrap(
      http.DELETE("/plans/templates/{template_id}/dates/{local_date}", {
        params: { path: { template_id: templateId, local_date: localDate } },
      }),
    );
  },
};
