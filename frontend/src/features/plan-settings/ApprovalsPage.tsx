import { EyeOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useSubmittedSopDrafts } from "../../domain/biz/hooks/useSopApprovals";
import type { SubmittedSopDraft } from "../../domain/biz/types";
import { templateApi } from "../../domain/plan/api";
import { usePendingDrafts } from "../../domain/plan/hooks/useTemplateChanges";
import { CHANGE_KIND_LABEL, draftErrorText, templateWriteMode } from "../../domain/plan/lib/templateChanges";
import type { PlanTemplate, TemplateChange } from "../../domain/plan/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";
import { DraftViewModal } from "./DraftViewModal";

const when = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D(dd) HH:mm");

/**
 * 承認待ち（Level 2、D.6b）: 全業務の、承認待ちのテンプレートの下書き。古い順（承認の順番待ち）。顧客で絞り込める（?client=）。
 * 行の「見る」で下書きの窓（今のデータでの確認・承認・差し戻し）を開く。見るのはだれでも、承認・差し戻しは業務管理者。
 * その下に、承認を依頼された SOP の草稿（P.4）。行から SOP の画面のその版へ移り、ステップを見て承認・差し戻しする。
 */
export function ApprovalsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const client = params.get("client") ?? undefined;
  const me = useCurrentStaff();
  const isAdmin = templateWriteMode(me.data) === "direct";
  const drafts = usePendingDrafts();
  const sopDrafts = useSubmittedSopDrafts();

  const clients = useMemo(
    () => [...new Set([
      ...(drafts.data ?? []).map((c) => c.client_code),
      ...(sopDrafts.data ?? []).map((d) => d.client_code),
    ].filter((c): c is string => !!c))].sort(),
    [drafts.data, sopDrafts.data],
  );
  const rows = (drafts.data ?? []).filter((c) => !client || c.client_code === client);
  const sopRows = (sopDrafts.data ?? []).filter((d) => !client || d.client_code === client);

  // 下書きの窓。変更・削除の下書きは、対象のテンプレートの今の値を読んでから開く（要約の「今」と確認に使う）
  const [viewer, setViewer] = useState<{ open: boolean; change: TemplateChange | null; template: PlanTemplate | null }>({
    open: false, change: null, template: null,
  });
  const [opening, setOpening] = useState<number | null>(null);
  const view = async (c: TemplateChange) => {
    if (c.template_id == null) return setViewer({ open: true, change: c, template: null });
    setOpening(c.id);
    try {
      const template = (await templateApi.list(c.biz_master_id)).find((t) => t.id === c.template_id) ?? null;
      setViewer({ open: true, change: c, template });
    } catch (e) {
      message.error(`テンプレートを読めませんでした: ${draftErrorText(e)}`);
    } finally {
      setOpening(null);
    }
  };
  const templatePage = (c: TemplateChange) => `/settings/templates?biz=${c.biz_master_id}${c.template_id ? `&template=${c.template_id}` : ""}`;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>承認待ち</h1>
          <Typography.Text type="secondary">
            リリース担当が書いた、予定テンプレートの下書きと SOP の草稿。古い順。{isAdmin ? "開いて、承認するか差し戻してください。" : "承認・差し戻しは業務管理者が行います。"}
          </Typography.Text>
        </div>
      </header>

      <label className="field">
        <span className="field-label">顧客</span>
        <Select<string>
          allowClear
          placeholder="すべての顧客"
          value={client}
          options={clients.map((c) => ({ value: c, label: c }))}
          onChange={(c) => setParams(c ? { client: c } : {})}
          style={{ width: 200 }}
        />
      </label>

      {drafts.error && (
        <Alert type="error" showIcon message={`承認待ちを取得できませんでした: ${drafts.error.message}`}
          action={<Button size="small" onClick={drafts.reload}>再読み込み</Button>} />
      )}

      <Typography.Title level={4} className="section-title">予定テンプレートの下書き <span className="muted mono">{rows.length} 件</span></Typography.Title>
      <Table<TemplateChange>
        rowKey="id"
        loading={drafts.loading}
        dataSource={rows}
        pagination={rows.length > 50 ? { pageSize: 50 } : false}
        locale={{ emptyText: <Empty description="承認待ちの下書きはありません" /> }}
        columns={[
          { title: "書いた日時", key: "when", width: 170, className: "mono", render: (_, c) => when(c.updated_at) },
          {
            title: "業務",
            key: "biz",
            render: (_, c) => (
              <Link to={templatePage(c)}>
                <span className="mono">{c.business_code}</span> {c.business_name}
              </Link>
            ),
          },
          { title: "テンプレート", key: "template", render: (_, c) => c.template_name ?? `#${c.template_id}` },
          { title: "種類", dataIndex: "kind", width: 96, render: (k: TemplateChange["kind"]) => <Tag>{CHANGE_KIND_LABEL[k]}</Tag> },
          { title: "書いた人", key: "drafter", width: 150, render: (_, c) => c.drafted_by_name ?? `職員 #${c.drafted_by_id}` },
          {
            title: "",
            key: "view",
            width: 96,
            render: (_, c) => (
              <Button type="link" size="small" icon={<EyeOutlined />} loading={opening === c.id} onClick={() => view(c)}>
                見る
              </Button>
            ),
          },
        ]}
      />

      <Typography.Title level={4} className="section-title">SOP の草稿 <span className="muted mono">{sopRows.length} 件</span></Typography.Title>
      {sopDrafts.error && (
        <Alert type="error" showIcon message={`SOP の承認待ちを取得できませんでした: ${sopDrafts.error.message}`}
          action={<Button size="small" onClick={sopDrafts.reload}>再読み込み</Button>} />
      )}
      <Table<SubmittedSopDraft>
        rowKey="version_id"
        loading={sopDrafts.loading}
        dataSource={sopRows}
        pagination={sopRows.length > 50 ? { pageSize: 50 } : false}
        locale={{ emptyText: <Empty description="承認を依頼された SOP の草稿はありません" /> }}
        columns={[
          { title: "依頼した日時", key: "when", width: 170, className: "mono", render: (_, d) => when(d.submitted_at) },
          {
            title: "業務",
            key: "biz",
            render: (_, d) => <><span className="mono">{d.business_code}</span> {d.biz_name}</>,
          },
          { title: "版", dataIndex: "version_tag", width: 160, className: "mono" },
          { title: "ステップ", dataIndex: "step_count", width: 84, className: "mono", render: (n: number) => `${n} 件` },
          { title: "依頼した人", key: "submitter", width: 150, render: (_, d) => d.submitted_by_name ?? `職員 #${d.submitted_by_id}` },
          {
            title: "",
            key: "open",
            width: 96,
            render: (_, d) => (
              <Link to={`/settings/sop?biz=${d.biz_master_id}&version=${d.version_id}`}>
                <EyeOutlined /> 開く
              </Link>
            ),
          },
        ]}
      />

      <DraftViewModal
        open={viewer.open}
        change={viewer.change}
        template={viewer.template}
        clientCode={viewer.change?.client_code ?? ""}
        me={me.data}
        onClose={() => setViewer((v) => ({ ...v, open: false }))}
        // 直すのは書いた本人。予定テンプレートの画面へ移って、そこの「変更依頼」から直す
        onEdit={(c) => navigate(templatePage(c))}
        onChanged={drafts.reload}
      />
    </div>
  );
}
