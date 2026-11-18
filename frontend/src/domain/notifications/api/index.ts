import { http, unwrap } from "../../../shared/api/http";
import { notifyFailuresChanged } from "../lib/failureEvents";
import type { FailedNotification, NotificationFailures } from "../types";

export const notificationApi = {
  /**
   * 届かなかったアラーム（GET /notifications/failures）: まだ確認されていないもの（古くても）と直近 7 日に確認したもの、
   * いま再試行中の配信の数。有効な職員なら誰でも（担当顧客が限られた職員には、その顧客の分だけ）。
   */
  failures(signal?: AbortSignal): Promise<NotificationFailures> {
    return unwrap(http.GET("/notifications/failures", { signal }));
  },

  /** 届かなかったアラームを「確認した」にする（作業者・確認者・リリース担当・業務管理者）（POST .../acknowledge-failure） */
  async acknowledge(notificationId: number): Promise<FailedNotification> {
    const result = await unwrap(http.POST("/notifications/{notification_id}/acknowledge-failure", {
      params: { path: { notification_id: notificationId } },
    }));
    notifyFailuresChanged();
    return result;
  },
};
