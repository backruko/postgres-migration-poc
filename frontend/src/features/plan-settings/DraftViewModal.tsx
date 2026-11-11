import { Alert, App, Button, Descriptions, Input, Modal, Space, Table, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { canPreviewPlanSettings } from "../../domain/biz/lib/settings";
import { templateChangeApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import {
  canChangeOwnDraft,
  CHANGE_KIND_LABEL,
  CHANGE_STATUS_COLOR,
  CHANGE_STATUS_LABEL,
  draftErrorText,
  templateWriteMode,
} from "../../domain/plan/lib/templateChanges";
import type { PlanTemplate, TemplateChange, TemplateDraftPreview } from "../../domain/plan/types";
import type { Staff } from "../../domain/staff/types";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";
import { changeSummary, summaryBaseline, type SummaryLine } from "./templateDraft";
import { DEFAULT_PREVIEW_MONTHS } from "./templatePreview";
import { TemplatePreviewPanel } from "./TemplatePreviewPanel";

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
  /** 取り下げ・承認・差し戻しのあと（一覧の再読み込み用） */
  onChanged: () => void;
}

const when = (iso: string | null | undefined) => (iso ? inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm") : "—");

/**
 * テンプレートの変更依頼を見る窓（Level 2、D.5 / D.6）。何を変えるか（前 → 下書き）と、だれがいつ書いたか・決めたか。
 * - 承認待ち: 今のデータで当てはめたら何が起きるか（予定の違い・オーダーへの影響）を自動で見せる（業務管理者・リリース担当）
 * - 業務管理者: 承認（ひとことは任意。自分の下書きも承認できる）・差し戻し（理由は必須）
 * - 書いた本人: 承認待ちの間だけ「直す」「取り下げる」
 * - 承認された変更は、承認の直前の値と比べる（その時に何が変わったか）
 */
export function DraftViewModal({ open, change, template, clientCode, me, onClose, onEdit, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const [busy, setBusy] = useState(false);
  const calendars = useBusinessCalendars();
  const sheets = useReferenceSheets(clientCode, open && change?.kind !== "delete");

  const pending = change?.status === "draft";
  const isAdmin = templateWriteMode(me) === "direct";
  const showPreview = pending && canPreviewPlanSettings(me);

  // 当てはめたら何が起きるか（承認待ちの間だけ。開くたび・範囲を変えるたびに今のデータで取り直す）
  const [months, setMonths] = useState<number>(DEFAULT_PREVIEW_MONTHS);
  const [preview, setPreview] = useState<{ data: TemplateDraftPreview | null; loading: boolean; error: string | null }>({
    data: null, loading: false, error: null,
  });
  // サーバーで動いている確認の数（範囲を切り替えると、前の確認はサーバーで最後まで動く）。
  // 確認はテンプレートの行をロックするため、残っている間に承認すると、その終わりを待つ（承認が遅く見える）。
  // 承認のボタンは、すべての確認が返ってくるまで押せないようにする
  const [running, setRunning] = useState(0);
  useEffect(() => {
    if (open) setMonths(DEFAULT_PREVIEW_MONTHS);
  }, [open, change?.id]);
  useEffect(() => {
    if (!open || !change || !showPreview) {
      setPreview({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false; // 範囲を変えた・閉じたあとに返ってきた結果は使わない（数は数える）
    setPreview((p) => ({ ...p, loading: true, error: null }));
    setRunning((n) => n + 1);
    templateChangeApi
      .preview(change.id, months)
      .then((data) => !cancelled && setPreview({ data, loading: false, error: null }))
      .catch((e: unknown) => !cancelled && setPreview({ data: null, loading: false, error: draftErrorText(e) }))
      .finally(() => setRunning((n) => n - 1));
    return () => {
      cancelled = true;
    };
  }, [open, change, showPreview, months]);

  // 承認・差し戻しの入力（承認のひとことは任意、差し戻しの理由は必須）
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [text, setText] = useState("");
  const openDecision = (d: "approve" | "reject") => {
    setText("");
    setDecision(d);
  };

  const baseline = useMemo(() => (change ? summaryBaseline(change, template) : null), [change, template]);
  const lines: SummaryLine[] | null = useMemo(() => {
    if (!change || !baseline) return null;
    if (change.kind === "update" && !baseline.template) return null; // テンプレートが削除済みで、変更前の値も無い
    return changeSummary(change, baseline.template, {
      calendar: (id) => calendars.data?.find((c) => c.id === id)?.name,
      sheet: (id) => sheets.data?.find((s) => s.id === id)?.name,
    });
  }, [change, baseline, calendars.data, sheets.data]);

  if (!change) return null;
  const mine = canChangeOwnDraft(change, me);
  const ownDraft = me?.id === change.drafted_by_id;

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

  const decide = async () => {
    if (!decision) return;
    if (decision === "reject" && !text.trim()) return;
    setBusy(true);
    try {
      if (decision === "approve") {
        await templateChangeApi.approve(change.id, text);
        message.success("承認しました。テンプレートに反映しました（未来の予定とオーダーも追従します）");
      } else {
        await templateChangeApi.reject(change.id, text.trim());
        message.success("差し戻しました。テンプレートは今の設定のままです");
      }
      setDecision(null);
      onClose();
    } catch (e) {
      // 承認で当てはめに失敗した場合は何も変わらず、下書きは承認待ちのまま
      message.error(`${decision === "approve" ? "承認" : "差し戻し"}できませんでした: ${draftErrorText(e)}`, 10);
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const decidedLabel = change.status === "withdrawn" ? "取り下げ" : change.status === "rejected" ? "差し戻した人" : "承認した人";

  return (
    <>
    <Modal
      open={open}
      title={`${CHANGE_KIND_LABEL[change.kind]}の下書き:${change.template_name ?? `テンプレート #${change.template_id}`}`}
      width={showPreview ? 900 : 720}
      onCancel={onClose}
      footer={
        <Space wrap>
          <Button onClick={onClose}>閉じる</Button>
          {mine && (
            <Button danger onClick={withdraw} loading={busy && decision === null}>
              取り下げる
            </Button>
          )}
          {mine && change.kind !== "delete" && (
            <Button onClick={() => onEdit(change)} disabled={busy}>
              直す
            </Button>
          )}
          {isAdmin && pending && (
            <Button danger onClick={() => openDecision("reject")} disabled={busy}>
              差し戻す
            </Button>
          )}
          {isAdmin && pending && (
            <Button type="primary" onClick={() => openDecision("approve")} disabled={busy || preview.loading || running > 0}
              title={running > 0 ? "確認が終わるまでお待ちください" : undefined}>
              承認する
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
          <Descriptions.Item label={decidedLabel}>
            {change.decided_by_name ?? `職員 #${change.decided_by_id}`}（{when(change.decided_at)}）
          </Descriptions.Item>
        )}
        {change.decision_note && (
          <Descriptions.Item label={change.status === "rejected" ? "差し戻しの理由" : "ひとこと"}>
            <Typography.Text type={change.status === "rejected" ? "danger" : undefined}>{change.decision_note}</Typography.Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {pending && !mine && !isAdmin && (
        <Alert type="info" showIcon className="modal-alert"
          message="承認待ちの下書きです。業務管理者が承認するまで、テンプレートは今の設定のままです（直す・取り下げるのは書いた本人だけ）。" />
      )}
      {!pending && baseline?.label === "今" && change.kind !== "create" && (
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
            { title: baseline?.label ?? "今", dataIndex: "before", className: "draft-before" },
            { title: change.kind === "delete" ? "下書き" : "下書きの内容", dataIndex: "after", className: "draft-after" },
          ]}
        />
      )}

      {showPreview && (
        <section className="draft-preview" aria-label="承認したら起きること">
          <Typography.Title level={5}>承認したら起きること（今のデータで確認）</Typography.Title>
          {preview.error && (
            <Alert type="error" showIcon className="modal-alert" message="確認できませんでした"
              description={`${preview.error}（このままでは承認もできません。差し戻すか、書いた人が直してください）`} />
          )}
          {preview.data?.create && (
            <TemplatePreviewPanel mode="create" preview={preview.data.create} tz={template?.timezone ?? TIMEZONE}
              months={months} onMonthsChange={setMonths} loading={preview.loading} />
          )}
          {preview.data?.change && (
            <TemplatePreviewPanel mode="update" preview={preview.data.change} tz={template?.timezone ?? TIMEZONE}
              months={months} onMonthsChange={setMonths} loading={preview.loading} />
          )}
          {!preview.data && preview.loading && <Typography.Text type="secondary">確認しています…</Typography.Text>}
        </section>
      )}
    </Modal>

    {/*
      承認・差し戻しの入力は、窓の「外」（兄弟）に置く。antd の Modal は閉じている間、中身を描き直さない
      （rc-dialog: shouldUpdate = visible || forceRender）。窓の中に置くと、承認が終わって窓と同時に閉じたとき、
      この入力が「反映しています…」のまま固まって閉じられなくなる（2026-11-11 に起きた）。
    */}
    <Modal
        open={decision !== null}
        title={decision === "approve" ? "この下書きを承認しますか？" : "この下書きを差し戻しますか？"}
        okText={decision === "approve" ? "承認する" : "差し戻す"}
        okButtonProps={{ danger: decision === "reject", disabled: decision === "reject" && !text.trim(), loading: busy }}
        cancelText="キャンセル"
        cancelButtonProps={{ disabled: busy }}
        closable={!busy}
        maskClosable={!busy}
        onOk={decide}
        onCancel={() => !busy && setDecision(null)}
        destroyOnClose
      >
        {busy && decision === "approve" && (
          <Alert type="info" showIcon className="modal-alert"
            message="反映しています。未来の予定とオーダーを作り直すため、テンプレートによっては少し時間がかかります。このままお待ちください。" />
        )}
        {decision === "approve" ? (
          <>
            <Typography.Paragraph>
              承認すると、すぐにテンプレートに反映します。未来の予定は作り直され、オーダーも追従します（上の確認のとおり）。
            </Typography.Paragraph>
            {ownDraft && (
              <Alert type="warning" showIcon className="modal-alert" message="自分の下書きを承認します（履歴に「自分の下書きを承認」と残ります）。" />
            )}
            <Input.TextArea rows={3} maxLength={500} showCount placeholder="ひとこと（任意）" value={text} onChange={(e) => setText(e.target.value)} />
          </>
        ) : (
          <>
            <Typography.Paragraph>差し戻すと、テンプレートは今の設定のままです。書いた人に理由が伝わります。</Typography.Paragraph>
            <Input.TextArea rows={3} maxLength={500} showCount placeholder="差し戻しの理由（必須）" value={text} onChange={(e) => setText(e.target.value)} />
          </>
        )}
      </Modal>
    </>
  );
}
