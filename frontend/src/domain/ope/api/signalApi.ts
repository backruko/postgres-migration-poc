import { http, unwrap } from "../../../shared/api/http";
import type { Signal } from "../types";

/** 顧客側の出来事の信号。受け取り（POST /ope/signals）は送信側の仕組みが行い、画面は一覧と人の対応だけを使う */
export const signalApi = {
  /** 人の対応が必要な信号（照合できず、まだ対応していないもの）（GET /ope/signals） */
  listOpen(signal?: AbortSignal): Promise<Signal[]> {
    return unwrap(http.GET("/ope/signals", { params: { query: { scope: "open" } }, signal }));
  },

  /** 照合できなかった信号に対応する（orderId を指定すると、そのオーダーを完了にしてから）（POST /ope/signals/{id}/handle） */
  handle(signalId: number, note: string, orderId?: number | null): Promise<Signal> {
    return unwrap(http.POST("/ope/signals/{signal_id}/handle", {
      params: { path: { signal_id: signalId } },
      body: { note, order_id: orderId ?? null },
    }));
  },
};
