import { http, unwrap } from "../../../shared/api/http";
import type { ReferenceSheet } from "../types";

/** 年間計画シート（参照のみ） */
export const sheetApi = {
  /** 顧客のシート一覧（マーカーと日付を含む）（GET /plans/sheets?client_code=） */
  listByClient(clientCode: string, signal?: AbortSignal): Promise<ReferenceSheet[]> {
    return unwrap(http.GET("/plans/sheets", { params: { query: { client_code: clientCode } }, signal }));
  },
};
