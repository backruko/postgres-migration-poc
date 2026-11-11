import { http, unwrap } from "../../../shared/api/http";
import type { BizMaster, BizMasterCreate, BizMasterUpdate, BizReleaseChange, BizReleaseUpdate, SopVersion } from "../types";

/**
 * 選択肢として一度に取得する業務の上限。
 * バックエンドの既定（limit=100）では業務が増えると一覧から漏れるため、明示的に指定する。
 */
const LIST_LIMIT = 1000;

export const bizApi = {
  /** 業務の一覧（論理削除されたものは含まれない）（GET /biz/） */
  list(signal?: AbortSignal): Promise<BizMaster[]> {
    return unwrap(http.GET("/biz/", { params: { query: { skip: 0, limit: LIST_LIMIT } }, signal }));
  },

  /** 顧客の業務の一覧（業務コード順）。includeDeleted で論理削除された業務も（GET /biz/?client_code=） */
  listForClient(clientCode: string, includeDeleted: boolean, signal?: AbortSignal): Promise<BizMaster[]> {
    return unwrap(
      http.GET("/biz/", {
        params: { query: { client_code: clientCode, include_deleted: includeDeleted, skip: 0, limit: LIST_LIMIT } },
        signal,
      }),
    );
  },

  /** 業務の登録（業務管理者。business_seq を省くと顧客の次の空き番号）（POST /biz/） */
  create(body: BizMasterCreate): Promise<BizMaster> {
    return unwrap(http.POST("/biz/", { body }));
  },

  /** 業務の削除（論理削除。業務管理者。テンプレート・未完了のオーダー・依存関係が残っていると 400）（DELETE /biz/{id}） */
  remove(bizId: number): Promise<BizMaster> {
    return unwrap(http.DELETE("/biz/{biz_id}", { params: { path: { biz_id: bizId } } }));
  },

  /** 削除した業務を元に戻す（業務管理者。顧客が有効なときだけ）（POST /biz/{id}/restore） */
  restore(bizId: number): Promise<BizMaster> {
    return unwrap(http.POST("/biz/{biz_id}/restore", { params: { path: { biz_id: bizId } } }));
  },

  /** 業務の設定の変更（送った項目だけ変わる）（PATCH /biz/{id}） */
  update(bizId: number, body: BizMasterUpdate): Promise<BizMaster> {
    return unwrap(http.PATCH("/biz/{biz_id}", { params: { path: { biz_id: bizId } }, body }));
  },

  /**
   * リリース設定（方法・時刻・何日前）だけの変更（リリース担当・業務管理者。送った項目だけ変わり、すぐに効く）
   * （PATCH /biz/{id}/release）。ほかの項目は送れない
   */
  updateRelease(bizId: number, body: BizReleaseUpdate): Promise<BizMaster> {
    return unwrap(http.PATCH("/biz/{biz_id}/release", { params: { path: { biz_id: bizId } }, body }));
  },

  /** リリース設定の変更履歴（新しい順。どの画面で変えたものも）（GET /biz/{id}/release-changes） */
  releaseChanges(bizId: number, signal?: AbortSignal): Promise<BizReleaseChange[]> {
    return unwrap(http.GET("/biz/{biz_id}/release-changes", { params: { path: { biz_id: bizId } }, signal }));
  },

  /** 承認済み（草稿以外）の SOP バージョン、新しい順。オーダーに指定できる選択肢（GET /biz/{id}/versions） */
  listVersions(bizId: number, signal?: AbortSignal): Promise<SopVersion[]> {
    return unwrap(http.GET("/biz/{biz_id}/versions", { params: { path: { biz_id: bizId } }, signal }));
  },
};
