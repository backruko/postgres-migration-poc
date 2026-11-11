import { Alert, Drawer, Empty, Spin, Tag, Timeline, Typography } from "antd";
import { sopApi } from "../../domain/biz/api/sopApi";
import { isSelfApproval, SOP_EVENT_COLOR, SOP_EVENT_LABEL } from "../../domain/biz/lib/sop";
import type { SopVersion, SopVersionEvent } from "../../domain/biz/types";
import { useTemplateChangesVersion } from "../../domain/plan/hooks/useTemplateChanges";
import { TIMEZONE } from "../../shared/config";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { dayjs, inZone } from "../../shared/lib/datetime";

interface Props {
  open: boolean;
  /** 履歴を見る版（閉じる間の表示が消えないよう、閉じても渡したままにする） */
  version: SopVersion | null;
  onClose: () => void;
}

const when = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm");
const who = (e: SopVersionEvent) => e.actor_name ?? (e.actor_id != null ? `職員 #${e.actor_id}` : "システム");

/** 1 件の説明（写し元・発効日・自分の草稿の承認・理由・ひとこと） */
function details(e: SopVersionEvent) {
  const d = e.detail ?? {};
  return (
    <>
      {e.event_type === "created" && typeof d.cloned_from_tag === "string" && (
        <div className="muted"><span className="mono">{d.cloned_from_tag}</span> を写して作成</div>
      )}
      {e.event_type === "approved" && typeof d.effective_date === "string" && (
        <div className="muted">
          {dayjs(d.effective_date).format("YYYY/M/D")} から有効
          {e.drafted_by_name && !isSelfApproval(e) && ` ・ ${e.drafted_by_name} の草稿`}
        </div>
      )}
      {e.note && (
        <Typography.Paragraph type={e.event_type === "rejected" ? "danger" : undefined} className="modal-note" style={{ margin: 0 }}>
          {e.event_type === "rejected" ? "理由: " : "ひとこと: "}{e.note}
        </Typography.Paragraph>
      )}
    </>
  );
}

/**
 * SOP の版の履歴（P.4）: 作成（写し元）・承認の依頼・取り下げ・差し戻し（理由）・承認（発効日・自分の草稿か）・承認の取消。
 * 新しい順。ステップの細かな編集は出さない（版の「最終編集」に最後の人と日時）。2026-11-11 より前の版には記録が無い。
 */
export function SopHistoryDrawer({ open, version, onClose }: Props) {
  const changes = useTemplateChangesVersion(); // 依頼・判断の直後に読み直す
  const events = useApiQuery((signal) => sopApi.events(version!.id, signal), [version?.id, changes], { enabled: open && !!version });
  const rows = [...(events.data ?? [])].reverse();

  return (
    <Drawer open={open} onClose={onClose} width={440} title={version ? `${version.version_tag} の履歴` : "履歴"} destroyOnClose>
      {events.error && <Alert type="error" showIcon message={`履歴を取得できませんでした: ${events.error.message}`} />}
      {events.loading && !events.data ? (
        <Spin />
      ) : rows.length === 0 ? (
        <Empty description="記録がありません（2026-11-11 より前に作られた版には記録がありません）" />
      ) : (
        <Timeline
          items={rows.map((e) => ({
            key: e.id,
            color: SOP_EVENT_COLOR[e.event_type] === "default" ? "gray" : SOP_EVENT_COLOR[e.event_type],
            children: (
              <div className="history-entry">
                <div className="history-head">
                  <span className="mono">{when(e.occurred_at)}</span>
                  <Tag color={SOP_EVENT_COLOR[e.event_type]}>{SOP_EVENT_LABEL[e.event_type]}</Tag>
                  {isSelfApproval(e) && <Tag>自分の草稿を承認</Tag>}
                  <span className="history-who">{who(e)}</span>
                </div>
                {details(e)}
              </div>
            ),
          }))}
        />
      )}
    </Drawer>
  );
}
