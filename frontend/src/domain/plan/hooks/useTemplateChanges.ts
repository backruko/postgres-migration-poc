import { useSyncExternalStore } from "react";
import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useAutoRefresh } from "../../../shared/hooks/useAutoRefresh";
import { templateChangeApi } from "../api";
import { subscribeTemplateChanges, templateChangesVersion } from "../lib/templateChangeEvents";
import { decidedWithin } from "../lib/templateChanges";
import type { TemplateChange } from "../types";

/** 承認待ちの件数・一覧の読み直し間隔（日次ダッシュボードと同じ 2 分。タブが見えている間だけ） */
const REFRESH_MS = 2 * 60 * 1000;

/** 変更依頼が変わるたびに変わる番号（作る・直す・取り下げる・承認・差し戻しが成功したとき） */
export function useTemplateChangesVersion(): number {
  return useSyncExternalStore(subscribeTemplateChanges, templateChangesVersion);
}

/** 承認待ちの下書きの件数（メニューのバッジ。業務管理者だけに出すので、それ以外は読まない） */
export function usePendingDraftCount(enabled: boolean) {
  const version = useTemplateChangesVersion();
  const query = useApiQuery((signal) => templateChangeApi.count({ status: "draft" }, signal), [version], { enabled });
  useAutoRefresh(() => enabled && query.reload(), REFRESH_MS);
  return query;
}

/** 全業務の承認待ちの下書き（古い順 = 承認の順番待ち）。「承認待ち」の画面 */
export function usePendingDrafts() {
  const version = useTemplateChangesVersion();
  const query = useApiQuery((signal) => templateChangeApi.list({ status: "draft", limit: 500 }, signal), [version]);
  useAutoRefresh(() => query.reload(), REFRESH_MS);
  return query;
}

export interface BizChangeRequests {
  /** 業務の承認待ちの下書き（種類を問わない。古い順） */
  pending: TemplateChange[];
  /** 自分が書いた下書きのうち、30 日以内に決まったもの（差し戻しの理由を見られるように。新しい順） */
  recentMine: TemplateChange[];
}

/** 業務の「変更依頼」の欄の中身を読む（承認待ちはだれの下書きでも、決まったものは自分の下書きだけ） */
export async function loadBizChangeRequests(
  bizId: number, myId: number | null, signal?: AbortSignal, now: Date = new Date(),
): Promise<BizChangeRequests> {
  const [pending, mine] = await Promise.all([
    templateChangeApi.list({ status: "draft", bizId }, signal),
    myId === null ? Promise.resolve([]) : templateChangeApi.list({ bizId, draftedBy: myId, limit: 50 }, signal),
  ]);
  return { pending, recentMine: mine.filter((c) => c.status !== "draft" && decidedWithin(c, now)) };
}

/** 予定テンプレートの画面の「変更依頼」の欄（Level 2）。業務が未指定の間は取得しない */
export function useBizChangeRequests(bizId: number | null, myId: number | null) {
  const version = useTemplateChangesVersion();
  return useApiQuery<BizChangeRequests>((signal) => loadBizChangeRequests(bizId as number, myId, signal), [bizId, myId, version], {
    enabled: bizId !== null,
  });
}
