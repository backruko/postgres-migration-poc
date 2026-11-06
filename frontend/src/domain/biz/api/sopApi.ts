import { http, unwrap } from "../../../shared/api/http";
import type { SopVersion, SopVersionDetail } from "../types";

/** SOP の版とステップ（M2.4〜）。書き込みは業務管理者だけ（バックエンドが判定する）。承認済みの版は変えられない */
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

  /** 草稿をステップごと削除（DELETE /biz/versions/{id}） */
  deleteDraft(versionId: number): Promise<SopVersion> {
    return unwrap(http.DELETE("/biz/versions/{version_id}", { params: { path: { version_id: versionId } } }));
  },
};
