import { http, unwrap } from "../../../shared/api/http";
import { notifyTemplateChangesChanged } from "../../plan/lib/templateChangeEvents";
import type { SopStep, SopStepCreate, SopStepUpdate, SopVersion, SopVersionDetail, SopVersionEvent, SubmittedSopDraft } from "../types";

/**
 * 承認待ちが変わったことを知らせる（業務管理者のメニューの件数・承認待ちの画面が、すぐに読み直す）。
 * テンプレートの下書きと同じ合図を使う（件数は両方の合計のため）
 */
async function notifying<T>(p: Promise<T>): Promise<T> {
  const result = await p;
  notifyTemplateChangesChanged();
  return result;
}

/**
 * SOP の版とステップ（M2.4〜）。草稿を書く・承認を依頼する・取り下げるのはリリース担当と業務管理者、
 * 承認・差し戻し・承認の取消は業務管理者（バックエンドが判定する。P.2）。承認済みの版・依頼中の草稿は変えられない
 */
export const sopApi = {
  /** 業務の版、新しい順。includeDrafts で草稿も（GET /biz/{id}/versions） */
  listVersions(bizId: number, includeDrafts: boolean, signal?: AbortSignal): Promise<SopVersion[]> {
    return unwrap(
      http.GET("/biz/{biz_id}/versions", { params: { path: { biz_id: bizId }, query: { include_drafts: includeDrafts } }, signal }),
    );
  },

  /** 版とそのステップ（番号順）（GET /biz/versions/{id}） */
  getVersion(versionId: number, signal?: AbortSignal): Promise<SopVersionDetail> {
    return unwrap(http.GET("/biz/versions/{version_id}", { params: { path: { version_id: versionId } }, signal }));
  },

  /** 新しい版: 最新の承認済みの版を写した草稿（承認済みが無ければ空）（POST /biz/{id}/versions/clone） */
  newDraft(bizId: number, versionTag: string): Promise<SopVersion> {
    return unwrap(http.POST("/biz/{biz_id}/versions/clone", { params: { path: { biz_id: bizId } }, body: { version_tag: versionTag } }));
  },

  /** 草稿のタグを変える（PATCH /biz/versions/{id}） */
  renameDraft(versionId: number, versionTag: string): Promise<SopVersion> {
    return unwrap(http.PATCH("/biz/versions/{version_id}", { params: { path: { version_id: versionId } }, body: { version_tag: versionTag } }));
  },

  /** 草稿にステップを追加（step_num を省くと最後）（POST /biz/versions/{id}/steps） */
  addStep(versionId: number, body: SopStepCreate): Promise<SopStep> {
    return unwrap(http.POST("/biz/versions/{version_id}/steps", { params: { path: { version_id: versionId } }, body }));
  },

  /** 草稿のステップを変える（送った項目だけ）（PATCH /biz/steps/{id}） */
  updateStep(stepId: number, body: SopStepUpdate): Promise<SopStep> {
    return unwrap(http.PATCH("/biz/steps/{step_id}", { params: { path: { step_id: stepId } }, body }));
  },

  /** 草稿のステップを削除（DELETE /biz/steps/{id}） */
  deleteStep(stepId: number): Promise<SopStep> {
    return unwrap(http.DELETE("/biz/steps/{step_id}", { params: { path: { step_id: stepId } } }));
  },

  /** 草稿のステップを stepIds の順に並べ、1 から振り直す（PUT /biz/versions/{id}/steps/order） */
  reorderSteps(versionId: number, stepIds: number[]): Promise<SopStep[]> {
    return unwrap(http.PUT("/biz/versions/{version_id}/steps/order", { params: { path: { version_id: versionId } }, body: { step_ids: stepIds } }));
  },

  /** 草稿を承認し、effectiveDate（YYYY-MM-DD、明日以降）の 0:00 日本時間から有効にする。note は任意のひとこと（空なら送らない） */
  approve(bizId: number, versionId: number, effectiveDate: string, note?: string): Promise<SopVersion> {
    const trimmed = note?.trim();
    return notifying(unwrap(
      http.POST("/biz/{biz_id}/versions/{draft_version_id}/approve", {
        params: { path: { biz_id: bizId, draft_version_id: versionId } },
        body: { effective_date: effectiveDate, note: trimmed ? trimmed : null },
      }),
    ));
  },

  /** 草稿の承認を依頼する（POST /biz/versions/{id}/submit）。依頼中は誰も直せない */
  submit(versionId: number): Promise<SopVersion> {
    return notifying(unwrap(http.POST("/biz/versions/{version_id}/submit", { params: { path: { version_id: versionId } } })));
  },

  /** 承認の依頼を取り下げ、草稿に戻す（依頼した本人か業務管理者）（POST /biz/versions/{id}/withdraw） */
  withdraw(versionId: number): Promise<SopVersion> {
    return notifying(unwrap(http.POST("/biz/versions/{version_id}/withdraw", { params: { path: { version_id: versionId } } })));
  },

  /** 依頼された草稿を差し戻す（業務管理者。理由は必須）（POST /biz/versions/{id}/reject） */
  reject(versionId: number, reason: string): Promise<SopVersion> {
    return notifying(unwrap(
      http.POST("/biz/versions/{version_id}/reject", { params: { path: { version_id: versionId } }, body: { reason: reason.trim() } }),
    ));
  },

  /** 版の記録（古い順）（GET /biz/versions/{id}/events） */
  events(versionId: number, signal?: AbortSignal): Promise<SopVersionEvent[]> {
    return unwrap(http.GET("/biz/versions/{version_id}/events", { params: { path: { version_id: versionId } }, signal }));
  },

  /** 承認を依頼された草稿（すべての業務。依頼の古い順）（GET /biz/sop-drafts/submitted） */
  listSubmitted(signal?: AbortSignal): Promise<SubmittedSopDraft[]> {
    return unwrap(http.GET("/biz/sop-drafts/submitted", { params: { query: {} }, signal }));
  },

  /** 承認を依頼された草稿の数（GET /biz/sop-drafts/submitted/count） */
  async countSubmitted(signal?: AbortSignal): Promise<number> {
    return (await unwrap(http.GET("/biz/sop-drafts/submitted/count", { signal }))).count;
  },

  /** 予定の版の承認を取り消し、草稿に戻す（POST /biz/{id}/versions/{v}/cancel） */
  cancelApproval(bizId: number, versionId: number): Promise<SopVersion> {
    return unwrap(
      http.POST("/biz/{biz_id}/versions/{future_version_id}/cancel", { params: { path: { biz_id: bizId, future_version_id: versionId } } }),
    );
  },

  /** 草稿をステップごと削除（DELETE /biz/versions/{id}） */
  deleteDraft(versionId: number): Promise<SopVersion> {
    return unwrap(http.DELETE("/biz/versions/{version_id}", { params: { path: { version_id: versionId } } }));
  },
};
