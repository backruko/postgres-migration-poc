import { Alert, App, Button, Descriptions, Modal, Space, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { templateChangeApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import {
  canChangeOwnDraft,
  CHANGE_KIND_LABEL,
  CHANGE_STATUS_COLOR,
  CHANGE_STATUS_LABEL,
  draftErrorText,
} from "../../domain/plan/lib/templateChanges";
import type { PlanTemplate, TemplateChange } from "../../domain/plan/types";
import type { Staff } from "../../domain/staff/types";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";
import { changeSummary, type SummaryLine } from "./templateDraft";

interface Props {
  open: boolean;
  /** 見る変更依頼（閉じる間の表示が消えないよう、閉じても渡したままにする） */
  change: TemplateChange | null;
  /** 変更・削除の対象のテンプレートの今の値（一覧から渡す。新規作成・削除済みのテンプレートは null） */
  template: PlanTemplate | null;
  clientCode: string;
  me: Staff | null | undefined;
  onClose: () => void;
  /** 「直す」: フォームを下書きの中身で開く */
  onEdit: (change: TemplateChange) => void;
  /** 取り下げたとき（一覧の再読み込み用） */
  onChanged: () => void;
}

const when = (iso: string | null | undefined) => (iso ? inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm") : "—");

/**
 * テンプレートの変更依頼を見る窓（Level 2、D.5）。何を変えるか（今 → 下書き）と、だれがいつ書いたか・決めたか。
 * 書いた本人は、承認待ちの間だけ「直す」「取り下げる」ができる（承認・差し戻しは業務管理者。D.6 でここに足す）。
 */
export function DraftViewModal({ open, change, template, clientCode, me, onClose, onEdit, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const [busy, setBusy] = useState(false);
  const calendars = useBusinessCalendars();
  const sheets = useReferenceSheets(clientCode, open && change?.kind !== "delete");

  const lines: SummaryLine[] | null = useMemo(() => {
    if (!change) return null;
    if (change.kind === "update" && !template) return null; // テンプレートが削除済み: 変更前の値が無い
    return changeSummary(change, template, {
      calendar: (id) => calendars.data?.find((c) => c.id === id)?.name,
      sheet: (id) => sheets.data?.find((s) => s.id === id)?.name,
    });
  }, [change, template, calendars.data, sheets.data]);

  if (!change) return null;
  const mine = canChangeOwnDraft(change, me);
  const pending = change.status === "draft";

  const withdraw = () =>
    modal.confirm({
      title: "この下書きを取り下げますか？",
      content: "取り下げた下書きは承認できなくなります（記録は残ります）。テンプレートは今の設定のままです。",
      okText: "取り下げる",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        setBusy(true);
        try {
          await templateChangeApi.withdraw(change.id);
          message.success("下書きを取り下げました");
          onClose();
        } catch (e) {
          message.error(`取り下げられませんでした: ${draftErrorText(e)}`, 8);
        } finally {
          setBusy(false);
          onChanged(); // 失敗しても読み直す（ほかの人が先に承認・差し戻しした場合など）
        }
      },
    });

  return (
    <Modal
      open={open}
      title={`${CHANGE_KIND_LABEL[change.kind]}の下書き: ${change.template_name ?? `テンプレート #${change.template_id}`}`}
      width={720}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>閉じる</Button>
          {mine && (
            <Button danger onClick={withdraw} loading={busy}>
              取り下げる
            </Button>
          )}
          {mine && change.kind !== "delete" && (
            <Button type="primary" onClick={() => onEdit(change)} disabled={busy}>
              直す
            </Button>
          )}
        </Space>
      }
    >
      <Descriptions size="small" column={1} className="draft-meta">
        <Descriptions.Item label="状態">
          <Tag color={CHANGE_STATUS_COLOR[change.status]}>{CHANGE_STATUS_LABEL[change.status]}</Tag>
          {change.is_direct && <Tag>業務管理者の直接の保存</Tag>}
          {change.self_approved && <Tag>自分の下書きを承認</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="業務">
          <span className="mono">{change.business_code}</span> {change.business_name}
        </Descriptions.Item>
        <Descriptions.Item label="書いた人">
          {change.drafted_by_name ?? `職員 #${change.drafted_by_id}`}（{when(change.drafted_at)}
          {change.updated_at !== change.drafted_at && pending ? `、最後に直したのは ${when(change.updated_at)}` : ""}）
        </Descriptions.Item>
        {!pending && (
          <Descriptions.Item label={change.status === "withdrawn" ? "取り下げ" : change.status === "rejected" ? "差し戻した人" : "承認した人"}>
            {change.decided_by_name ?? `職員 #${change.decided_by_id}`}（{when(change.decided_at)}）
          </Descriptions.Item>
        )}
        {change.decision_note && (
          <Descriptions.Item label={change.status === "rejected" ? "差し戻しの理由" : "ひとこと"}>
            <Typography.Text type={change.status === "rejected" ? "danger" : undefined}>{change.decision_note}</Typography.Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {pending && !mine && (
        <Alert type="info" showIcon className="modal-alert"
          message="承認待ちの下書きです。業務管理者が承認するまで、テンプレートは今の設定のままです（直す・取り下げるのは書いた本人だけ）。" />
      )}
      {!pending && change.kind !== "create" && (
        <Typography.Paragraph type="secondary" className="modal-note">
          「今」は、いまのテンプレートの値です（下書きを書いたときの値ではありません）。
        </Typography.Paragraph>
      )}

      {lines === null ? (
        <Alert type="warning" showIcon message="テンプレートが削除されているため、変更の前の値は表示できません。" />
      ) : (
        <Table<SummaryLine>
          rowKey="label"
          size="small"
          pagination={false}
          dataSource={lines}
          columns={[
            { title: "項目", dataIndex: "label", width: 150 },
            { title: "今", dataIndex: "before", className: "draft-before" },
            { title: change.kind === "delete" ? "下書き" : "下書きの内容", dataIndex: "after", className: "draft-after" },
          ]}
        />
      )}
    </Modal>
  );
}
