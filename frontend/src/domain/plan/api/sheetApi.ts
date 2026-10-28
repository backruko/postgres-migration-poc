import { http, unwrap } from "../../../shared/api/http";
import type { CreateMarkerInput, ReferenceSheet, ReferenceSheetMarker, UpdateMarkerInput } from "../types";

/** 年間計画シート（参照と、マーカーの追加・変更） */
export const sheetApi = {
  /** 顧客のシート一覧（マーカーと日付を含む）（GET /plans/sheets?client_code=） */
  listByClient(clientCode: string, signal?: AbortSignal): Promise<ReferenceSheet[]> {
    return unwrap(http.GET("/plans/sheets", { params: { query: { client_code: clientCode } }, signal }));
  },

  /**
   * マーカーを追加する（POST /plans/sheets/{id}/markers）。rule を指定すると、シートの期間全体の日付をルールから生成する。
   * 戻り値はシート全体。
   */
  addMarker(sheetId: number, input: CreateMarkerInput): Promise<ReferenceSheet> {
    return unwrap(http.POST("/plans/sheets/{sheet_id}/markers", { params: { path: { sheet_id: sheetId } }, body: input }));
  },

  /**
   * マーカーの説明・生成ルールを変更する（PATCH /plans/sheets/{id}/markers/{marker}）。
   * ルールを変えて日付が動くと、このマーカーを参照する全テンプレートの未来の予定が作り直される（人が編集した日付は維持）。
   */
  updateMarker(sheetId: number, marker: string, input: UpdateMarkerInput): Promise<ReferenceSheetMarker> {
    return unwrap(
      http.PATCH("/plans/sheets/{sheet_id}/markers/{marker}", { params: { path: { sheet_id: sheetId, marker } }, body: input }),
    );
  },
};
