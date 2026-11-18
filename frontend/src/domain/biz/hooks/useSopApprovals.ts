import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { useAutoRefresh } from "../../../shared/hooks/useAutoRefresh";
import { useTemplateChangesVersion } from "../../plan/hooks/useTemplateChanges";
import { sopApi } from "../api/sopApi";

/** 読み直しの間隔（テンプレートの承認待ちと同じ 2 分。タブが見えている間だけ） */
const REFRESH_MS = 2 * 60 * 1000;

/**
 * 承認を依頼された SOP の草稿の数（業務管理者のメニューのバッジ。テンプレートの下書きの数と合わせて出す）。
 * 承認待ちが変わった合図（依頼・取り下げ・差し戻し・承認）で読み直す
 */
export function useSubmittedSopDraftCount(enabled: boolean) {
  const version = useTemplateChangesVersion();
  const query = useApiQuery((signal) => sopApi.countSubmitted(signal), [version], { enabled });
  useAutoRefresh(() => enabled && query.reload(), REFRESH_MS);
  return query;
}

/** 承認を依頼された SOP の草稿（すべての業務。依頼の古い順）。「承認待ち」の画面 */
export function useSubmittedSopDrafts() {
  const version = useTemplateChangesVersion();
  const query = useApiQuery((signal) => sopApi.listSubmitted(signal), [version]);
  useAutoRefresh(() => query.reload(), REFRESH_MS);
  return query;
}
