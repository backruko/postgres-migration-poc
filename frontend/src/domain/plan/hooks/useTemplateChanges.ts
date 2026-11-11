import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { templateChangeApi } from "../api";
import { decidedWithin } from "../lib/templateChanges";
import type { TemplateChange } from "../types";

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
  return useApiQuery<BizChangeRequests>((signal) => loadBizChangeRequests(bizId as number, myId, signal), [bizId, myId], {
    enabled: bizId !== null,
  });
}
