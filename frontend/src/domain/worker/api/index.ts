import { http, unwrap } from "../../../shared/api/http";
import type { WorkerErrorList, WorkerHealthState, WorkerHealthSummary } from "../types";

export const workerApi = {
  /**
   * ワーカーが動いているか（GET /health/worker。ログインなし）。動いていれば 200、心拍が 5 分より古い・止まっている・
   * 一度も動いていなければ 503 で、どちらも同じ形の本文を返す（503 もエラーにせず、その本文を返す）
   */
  async health(signal?: AbortSignal): Promise<WorkerHealthState> {
    const result = await http.GET("/health/worker", { signal });
    if (result.response.status === 503 && result.error && typeof result.error === "object" && "state" in result.error) {
      return result.error as WorkerHealthState;
    }
    return unwrap(Promise.resolve(result));
  },

  /** 日本時間の 1 日ごとのまとめと期間の合計（GET /worker-health/summary。from / to は YYYY-MM-DD、最大 62 日） */
  summary(params: { from: string; to: string }, signal?: AbortSignal): Promise<WorkerHealthSummary> {
    return unwrap(http.GET("/worker-health/summary", { params: { query: { from: params.from, to: params.to } }, signal }));
  },

  /** 受け止めたエラーの一覧（新しい順。トレースバックつき）（GET /worker-health/errors） */
  errors(params: { from: string; to: string; limit?: number }, signal?: AbortSignal): Promise<WorkerErrorList> {
    return unwrap(
      http.GET("/worker-health/errors", { params: { query: { from: params.from, to: params.to, limit: params.limit ?? 200 } }, signal }),
    );
  },
};
