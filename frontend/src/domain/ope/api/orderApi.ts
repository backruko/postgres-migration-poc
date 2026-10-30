import { http, unwrap } from "../../../shared/api/http";
import type { ConflictAction, OrderDetail } from "../types";

/**
 * オーダーの参照と状態の操作。どのリクエストにも、操作している職員（X-Staff-Id）が http.ts で付く。
 * 誰が何をできるかはバックエンドが判定する（画面の出し分けは domain/ope/lib/orderActions.ts）。
 */
const path = (orderId: number) => ({ params: { path: { order_id: orderId } } });

export const orderApi = {
  /** オーダーと、その記録（古い順）（GET /ope/orders/{id}） */
  get(orderId: number, signal?: AbortSignal): Promise<OrderDetail> {
    return unwrap(http.GET("/ope/orders/{order_id}", { ...path(orderId), signal }));
  },

  /** pending → ready（POST /ope/orders/{id}/release） */
  release(orderId: number, comment?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/release", { ...path(orderId), body: { comment: comment || null } }));
  },

  /** 予定日より前の開始を承認（POST /ope/orders/{id}/approve-early-start） */
  approveEarlyStart(orderId: number, comment?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/approve-early-start", { ...path(orderId), body: { comment: comment || null } }));
  },

  /** ready → in_progress（POST /ope/orders/{id}/start） */
  start(orderId: number, comment?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/start", { ...path(orderId), body: { comment: comment || null } }));
  },

  /** → on_hold（理由必須）（POST /ope/orders/{id}/hold） */
  hold(orderId: number, reason: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/hold", { ...path(orderId), body: { reason } }));
  },

  /** on_hold → 保留前の状態（POST /ope/orders/{id}/resume） */
  resume(orderId: number, comment?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/resume", { ...path(orderId), body: { comment: comment || null } }));
  },

  /** in_progress → finished（POST /ope/orders/{id}/finish） */
  finish(orderId: number, comment?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/finish", { ...path(orderId), body: { comment: comment || null } }));
  },

  /** → canceled（理由必須。plan の予定も取り消す）（POST /ope/orders/{id}/cancel） */
  cancel(orderId: number, reason: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/cancel", { ...path(orderId), body: { reason } }));
  },

  /** 矛盾の解決（cancel は理由必須）（POST /ope/orders/{id}/resolve-conflict） */
  resolveConflict(orderId: number, action: ConflictAction, reason?: string): Promise<OrderDetail> {
    return unwrap(http.POST("/ope/orders/{order_id}/resolve-conflict", { ...path(orderId), body: { action, reason: reason || null } }));
  },
};
