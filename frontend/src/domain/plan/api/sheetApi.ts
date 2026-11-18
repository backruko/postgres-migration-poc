import { http, unwrap } from "../../../shared/api/http";
import type {
  CreateMarkerInput,
  CreateSheetInput,
  SheetEventsBatchInput,
  UpdateSheetInput,
  ReferenceSheet,
  ReferenceSheetEvent,
  ReferenceSheetMarker,
  SheetImportInput,
  SheetImportResult,
  UpdateMarkerInput,
} from "../types";

/**
 * 年間計画シート（参照と、マーカー・日付の追加・変更）。
 * 日付を変えると、そのマーカーを参照する全テンプレートの未来の予定がバックエンドで作り直される。
 */
export const sheetApi = {
  /** 顧客のシート一覧（マーカーと日付を含む）（GET /plans/sheets?client_code=） */
  listByClient(clientCode: string, signal?: AbortSignal): Promise<ReferenceSheet[]> {
    return unwrap(http.GET("/plans/sheets", { params: { query: { client_code: clientCode } }, signal }));
  },

  /**
   * シート（マーカーと日付）を一括で取り込む（POST /plans/sheets/import）。顧客コード + シート名が同じシートがあれば更新、無ければ作る。
   * replaceEvents が true なら、ファイルに無い日付を削除してシートをファイルに合わせる（人が変更・追加・取消した日付は残る）。
   * 全件が 1 つのまとまりで、エラーのときは何も反映されない。
   */
  importSheet(input: SheetImportInput, replaceEvents: boolean): Promise<SheetImportResult> {
    return unwrap(http.POST("/plans/sheets/import", { params: { query: { replace_events: replaceEvents } }, body: input }));
  },

  /** 空のシートを作る（POST /plans/sheets）。マーカーと日付は、あとから追加する */
  createSheet(input: CreateSheetInput): Promise<ReferenceSheet> {
    return unwrap(http.POST("/plans/sheets", { body: input }));
  },

  /**
   * シートの名前・期間を変える（PATCH /plans/sheets/{id}。送った項目だけ）。期間を延ばすとルールのマーカーが新しい期間まで作る。
   * 縮めるのは、新しい期間の外に残る日付が無いときだけ（あれば 400）
   */
  updateSheet(sheetId: number, input: UpdateSheetInput): Promise<ReferenceSheet> {
    return unwrap(http.PATCH("/plans/sheets/{sheet_id}", { params: { path: { sheet_id: sheetId } }, body: input }));
  },

  /** マーカーとその日付を削除する（DELETE /plans/sheets/{id}/markers/{marker}）。テンプレート・下書きが参照していれば 409 */
  deleteMarker(sheetId: number, marker: string): Promise<ReferenceSheet> {
    return unwrap(http.DELETE("/plans/sheets/{sheet_id}/markers/{marker}", { params: { path: { sheet_id: sheetId, marker } } }));
  },

  /**
   * マーカーの日付をまとめて足す・消す（POST /plans/sheets/{id}/markers/{marker}/events/batch）。1 回のまとまりで、
   * 参照しているテンプレートの予定は最後に 1 回だけ作り直される
   */
  changeEvents(sheetId: number, marker: string, input: SheetEventsBatchInput): Promise<ReferenceSheetMarker> {
    return unwrap(
      http.POST("/plans/sheets/{sheet_id}/markers/{marker}/events/batch", { params: { path: { sheet_id: sheetId, marker } }, body: input }),
    );
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

  /** マーカーに日付を追加する（登録済みの日付は無視、取消した日付は取消を解除）（POST /plans/sheets/{id}/markers/{marker}/events） */
  addEvents(sheetId: number, marker: string, dates: string[]): Promise<ReferenceSheetMarker> {
    return unwrap(
      http.POST("/plans/sheets/{sheet_id}/markers/{marker}/events", {
        params: { path: { sheet_id: sheetId, marker } },
        body: { dates },
      }),
    );
  },

  /** 日付を動かす。生成マーカーでは人の編集として記録され、再生成で上書きされない（PATCH /plans/sheets/events/{id}） */
  moveEvent(eventId: number, eventDate: string): Promise<ReferenceSheetEvent> {
    return unwrap(
      http.PATCH("/plans/sheets/events/{event_id}", { params: { path: { event_id: eventId } }, body: { event_date: eventDate } }),
    );
  },

  /** 生成マーカーの日付への人の編集・取消を取り消し、ルールの日付に戻す（POST /plans/sheets/events/{id}/revert） */
  revertEvent(eventId: number): Promise<ReferenceSheetEvent> {
    return unwrap(http.POST("/plans/sheets/events/{event_id}/revert", { params: { path: { event_id: eventId } } }));
  },

  /** 日付を削除する。ルール由来の日付は、再生成で復活しないよう「取消」として記録が残る（DELETE /plans/sheets/events/{id}） */
  deleteEvent(eventId: number): Promise<ReferenceSheetEvent> {
    return unwrap(http.DELETE("/plans/sheets/events/{event_id}", { params: { path: { event_id: eventId } } }));
  },
};
