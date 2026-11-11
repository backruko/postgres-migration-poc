import { http, unwrap } from "../../../shared/api/http";
import type { TemplateChange, TemplateChangeStatus, TemplateDraftInput, TemplateDraftPreview } from "../types";

/** 一覧の絞り込み（すべて省略可） */
export interface TemplateChangeFilter {
  status?: TemplateChangeStatus;
  bizId?: number;
  templateId?: number;
  draftedBy?: number;
  limit?: number;
}

/**
 * テンプレートの変更依頼（下書き → 承認 → 反映。Level 2）。
 * 下書きはリリース担当・業務管理者が書き、直す・取り下げるのは本人だけ。承認・差し戻しは業務管理者（D.6 で画面に出す）。
 * 読むのはログインしている職員なら誰でも。
 */
export const templateChangeApi = {
  /**
   * 変更依頼の一覧（GET /plans/template-changes）。承認待ち（status=draft）は古い順＝承認の順番待ち、それ以外は新しい順。
   */
  list(filter: TemplateChangeFilter = {}, signal?: AbortSignal): Promise<TemplateChange[]> {
    const query = {
      status: filter.status,
      biz_id: filter.bizId,
      template_id: filter.templateId,
      drafted_by: filter.draftedBy,
      limit: filter.limit,
    };
    return unwrap(http.GET("/plans/template-changes", { params: { query }, signal }));
  },

  /** 件数（既定は承認待ち。メニューのバッジ用） */
  async count(filter: Pick<TemplateChangeFilter, "status" | "bizId" | "draftedBy"> = {}, signal?: AbortSignal): Promise<number> {
    const query = { status: filter.status, biz_id: filter.bizId, drafted_by: filter.draftedBy };
    const result = await unwrap(http.GET("/plans/template-changes/count", { params: { query }, signal }));
    return result.count;
  },

  get(changeId: number, signal?: AbortSignal): Promise<TemplateChange> {
    return unwrap(http.GET("/plans/template-changes/{change_id}", { params: { path: { change_id: changeId } }, signal }));
  },

  /** テンプレートの変更履歴（新しい順。削除済みのテンプレートも読める） */
  history(templateId: number, signal?: AbortSignal): Promise<TemplateChange[]> {
    return unwrap(http.GET("/plans/templates/{template_id}/changes", { params: { path: { template_id: templateId } }, signal }));
  },

  /**
   * 下書きを作る（POST /plans/template-changes）。本物の保存と同じ処理で確かめられ、誤りは直接の保存と同じエラー。
   * 何も変えない下書きは 400、承認待ちの下書きがあるテンプレートには 409 TEMPLATE_DRAFT_PENDING。
   */
  create(input: TemplateDraftInput): Promise<TemplateChange> {
    return unwrap(http.POST("/plans/template-changes", { body: input }));
  },

  /** 本人が承認待ちの下書きを直す（中身をまるごと置き換える。種類と対象のテンプレートは変えられない） */
  edit(changeId: number, input: TemplateDraftInput): Promise<TemplateChange> {
    return unwrap(http.PUT("/plans/template-changes/{change_id}", { params: { path: { change_id: changeId } }, body: input }));
  },

  /** 本人が承認待ちの下書きを取り下げる（記録は残る） */
  withdraw(changeId: number): Promise<TemplateChange> {
    return unwrap(http.POST("/plans/template-changes/{change_id}/withdraw", { params: { path: { change_id: changeId } } }));
  },

  /**
   * 承認待ちの下書きを、今のデータで当てはめたら何が起きるか（POST /ops/template-changes/{id}/preview）。何も保存されない。
   * 新規作成は create、変更（項目・指定日）・削除は change に入る。リリース担当・業務管理者。
   */
  preview(changeId: number, months: number): Promise<TemplateDraftPreview> {
    return unwrap(
      http.POST("/ops/template-changes/{change_id}/preview", { params: { path: { change_id: changeId }, query: { months } } }),
    );
  },
};
