import { http, unwrap } from "../../../shared/api/http";
import type {
  CancelInstanceInput,
  CancelledInstance,
  OneOffInstanceInput,
  OverrideInstanceInput,
  PlanInstance,
  RevertInstanceInput,
  RevertResult,
} from "../types";

/**
 * 予定（インスタンス）の手動操作。
 * インスタンスは不変のため、どの操作も元の行は変更せず「無効化 + 追記」で記録される（バックエンド側の仕様）。
 */
export const instanceApi = {
  /** 1件だけ取り消す。以後の再生成でも復活しない（POST /plans/instances/{id}/cancel） */
  cancel(instanceId: number, input: CancelInstanceInput = {}): Promise<PlanInstance> {
    return unwrap(
      http.POST("/plans/instances/{instance_id}/cancel", { params: { path: { instance_id: instanceId } }, body: input }),
    );
  },

  /** 1件だけ日時を変更する（延期・前倒し）。テンプレートのルールは変わらない（POST /plans/instances/{id}/override） */
  override(instanceId: number, input: OverrideInstanceInput): Promise<PlanInstance> {
    return unwrap(
      http.POST("/plans/instances/{instance_id}/override", { params: { path: { instance_id: instanceId } }, body: input }),
    );
  },

  /** 手動変更・取消を取り消し、ルールの日時に戻す（POST /plans/instances/{id}/revert） */
  revert(instanceId: number, input: RevertInstanceInput = {}): Promise<RevertResult> {
    return unwrap(
      http.POST("/plans/instances/{instance_id}/revert", { params: { path: { instance_id: instanceId } }, body: input }),
    );
  },

  /** 取り消した予定を、取消の直前と同じ日時で元に戻す（POST /plans/instances/{id}/restore） */
  restore(instanceId: number): Promise<PlanInstance> {
    return unwrap(http.POST("/plans/instances/{instance_id}/restore", { params: { path: { instance_id: instanceId } } }));
  },

  /** 顧客の取り消された予定の一覧。カレンダーには表示されない（GET /plans/clients/{client_code}/cancelled） */
  listCancelled(clientCode: string, start: string, end: string, signal?: AbortSignal): Promise<CancelledInstance[]> {
    return unwrap(
      http.GET("/plans/clients/{client_code}/cancelled", {
        params: { path: { client_code: clientCode }, query: { start, end } },
        signal,
      }),
    );
  },

  /** 発生枠に紐づかない臨時の予定を追加する（POST /plans/templates/{id}/instances） */
  addOneOff(templateId: number, input: OneOffInstanceInput): Promise<PlanInstance> {
    return unwrap(
      http.POST("/plans/templates/{template_id}/instances", { params: { path: { template_id: templateId } }, body: input }),
    );
  },
};
