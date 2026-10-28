import { http, unwrap } from "../../../shared/api/http";
import type { BizMaster } from "../types";

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
};
