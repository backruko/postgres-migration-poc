import { http, unwrap } from "../../../shared/api/http";
import type {
  CreatePlanTemplateInput,
  PlanTemplate,
  TemplateChangePreview,
  TemplateCreatePreview,
  UpdatePlanTemplateInput,
} from "../types";

/** 予定テンプレート（事前設定）。参照・作成・編集・削除 */
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

  /**
   * この内容で作ったらできる予定を確かめる（POST /ops/templates/preview）。何も保存されない。
   * バックエンドが実際の作成を試し実行して取り消すので、休日の扱いなども作ったときと同じになる。入力の誤りは作成と同じエラー。
   */
  previewCreate(input: CreatePlanTemplateInput, months: number): Promise<TemplateCreatePreview> {
    return unwrap(http.POST("/ops/templates/preview", { params: { query: { months } }, body: input }));
  },

  /**
   * この内容で変えたら何が起きるかを確かめる（POST /ops/templates/{id}/preview）。何も保存されない。
   * 予定の違い（増える・無くなる・日時が変わる）と、追従するオーダー（日時が変わる・取消・矛盾）が返る。入力の誤りは更新と同じエラー。
   */
  previewUpdate(templateId: number, input: UpdatePlanTemplateInput, months: number): Promise<TemplateChangePreview> {
    return unwrap(
      http.POST("/ops/templates/{template_id}/preview", {
        params: { path: { template_id: templateId }, query: { months } },
        body: input,
      }),
    );
  },

  /**
   * テンプレートを削除する（DELETE /plans/templates/{id}）。
   * 論理削除: 未来の予定は無効化され、過去の予定は履歴として残る（POST /plans/templates/{id}/restore で復元できる）。
   */
  delete(templateId: number): Promise<PlanTemplate> {
    return unwrap(http.DELETE("/plans/templates/{template_id}", { params: { path: { template_id: templateId } } }));
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
