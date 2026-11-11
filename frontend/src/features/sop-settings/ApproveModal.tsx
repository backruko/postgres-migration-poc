import { Alert, App, DatePicker, Empty, Input, Modal, Space, Spin, Tag, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";
import { sopApi } from "../../domain/biz/api/sopApi";
import { firstEffectiveDate, isOwnDraft, isSubmitted } from "../../domain/biz/lib/sop";
import type { SopVersion } from "../../domain/biz/types";
import { sopImpactApi } from "../../domain/ope/api";
import { ORDER_STATUS_COLOR, ORDER_STATUS_LABEL } from "../../domain/ope/lib/orderActions";
import type { SopImpactGroup, SopImpactOrder } from "../../domain/ope/types";
import type { Staff } from "../../domain/staff/types";
import { TIMEZONE } from "../../shared/config";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { dayjs, inZone, todayIn } from "../../shared/lib/datetime";

interface Props {
  open: boolean;
  bizId: number;
  draft: SopVersion | null;
  /** いま有効な版（発効日の前日で終わる。無ければ null） */
  current: SopVersion | null;
  /** 承認する業務管理者（自分の草稿の承認なら、そう知らせる） */
  me: Staff | null | undefined;
  onClose: () => void;
  onApproved: () => void;
}

const LIST_LIMIT = 20;
const when = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm");

/**
 * 草稿の承認。発効日（明日以降、その日の 0:00 日本時間から）を選ぶと、影響（どのオーダーが新しい版を使うか）を
 * 承認する前に見せる。承認すると状態は「予定」になり、発効日に「現行」になる。
 */
export function ApproveModal({ open, bizId, draft, current, me, onClose, onApproved }: Props) {
  const { message } = App.useApp();
  const first = firstEffectiveDate(todayIn(TIMEZONE));
  const [date, setDate] = useState<string>(first);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDate(firstEffectiveDate(todayIn(TIMEZONE)));
      setNote("");
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  const impact = useApiQuery((signal) => sopImpactApi.get(bizId, date, LIST_LIMIT, signal), [bizId, date], { enabled: open });

  const approve = async () => {
    if (!draft) return;
    setSubmitting(true);
    setError(null);
    try {
      const approved = await sopApi.approve(bizId, draft.id, date, note);
      message.success(`${approved.version_tag} を承認しました（${dayjs(date).format("YYYY/M/D")} から有効）。`, 6);
      onClose();
      onApproved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "承認できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  const fromLabel = dayjs(date).format("YYYY/M/D");
  const dayBefore = dayjs(date).subtract(1, "day").format("YYYY/M/D");

  return (
    <Modal
      open={open}
      title={draft ? `${draft.version_tag} を承認` : "承認"}
      okText="承認する"
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => void approve()}
      okButtonProps={{ disabled: !draft || impact.loading }}
      onCancel={onClose}
      width={680}
      destroyOnClose
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">発効日（この日の 0:00 から）</span>
          <DatePicker
            value={dayjs(date)}
            allowClear={false}
            disabledDate={(d: Dayjs) => d.format("YYYY-MM-DD") < first}
            onChange={(d: Dayjs | null) => d && setDate(d.format("YYYY-MM-DD"))}
          />
        </label>
        <Typography.Text>
          {fromLabel} 以降に予定されたオーダーが <span className="mono">{draft?.version_tag}</span> を使います。
          {current ? (
            <> 現行の <span className="mono">{current.version_tag}</span> は {dayBefore} までになります。</>
          ) : (
            " この業務には今有効な版がありません。"
          )}
          承認すると内容は変えられません（取り消せるのは発効日の前まで）。
        </Typography.Text>

        {impact.error && <Alert type="error" showIcon message={`影響を確認できませんでした: ${impact.error.message}`} />}
        {impact.loading && !impact.data ? (
          <Spin />
        ) : impact.data ? (
          <div className="sop-impact">
            <ImpactGroup title="新しい版を使うオーダー" note="まだリリースされていない、版を指定していないオーダー" group={impact.data.will_switch} />
            <ImpactGroup title="指定した版のままのオーダー" note="リリース担当が版を指定（固定）している" group={impact.data.pinned} tag={(o) => o.pinned_sop_version_tag} />
            <ImpactGroup title="リリース済み（固定した版のまま）" note="リリースのときに版とステップが固定された" group={impact.data.released} tag={(o) => o.frozen_sop_version_tag ?? "版なし"} />
          </div>
        ) : null}
        {draft && isSubmitted(draft) && (
          <Typography.Text type="secondary">
            {draft.submitted_by_name ?? `職員 #${draft.submitted_by_id}`} が {when(draft.submitted_at!)} に承認を依頼しました。
          </Typography.Text>
        )}
        {draft && isOwnDraft(draft, me) && (
          <Alert type="warning" showIcon message="自分の草稿を承認します（記録に「自分の草稿を承認」と残ります）。" />
        )}
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">ひとこと（任意。記録に残ります）</span>
          <Input.TextArea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoSize={{ minRows: 1, maxRows: 4 }} />
        </label>
        {error && <Alert type="error" showIcon message={error} />}
      </Space>
    </Modal>
  );
}

function ImpactGroup({ title, note, group, tag }: { title: string; note: string; group: SopImpactGroup; tag?: (o: SopImpactOrder) => string | null }) {
  return (
    <section className="sop-impact-group">
      <div className="sop-impact-head">
        <strong>{title}</strong> <span className="mono">{group.total} 件</span>
        <span className="muted"> ・ {note}</span>
      </div>
      {group.total === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="なし" style={{ margin: "4px 0" }} />
      ) : (
        <ul className="sop-impact-list">
          {group.orders.map((o) => (
            <li key={o.id}>
              <span className="mono">{when(o.scheduled_start)}</span> {o.template_name}{" "}
              <Tag color={ORDER_STATUS_COLOR[o.status]}>{ORDER_STATUS_LABEL[o.status]}</Tag>
              {tag && tag(o) && <Tag className="mono">{tag(o)}</Tag>}
              <span className="muted mono"> #{o.id}</span>
            </li>
          ))}
          {group.total > group.orders.length && <li className="muted">ほか {group.total - group.orders.length} 件</li>}
        </ul>
      )}
    </section>
  );
}
