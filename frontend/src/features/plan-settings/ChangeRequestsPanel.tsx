import { EyeOutlined } from "@ant-design/icons";
import { Alert, Button, Table, Tag, Typography } from "antd";
import type { BizChangeRequests } from "../../domain/plan/hooks/useTemplateChanges";
import { CHANGE_KIND_LABEL, CHANGE_STATUS_COLOR, CHANGE_STATUS_LABEL } from "../../domain/plan/lib/templateChanges";
import type { TemplateChange } from "../../domain/plan/types";
import type { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";

interface Props {
  data: BizChangeRequests | null;
  loading: boolean;
  error: ApiError | null;
  onReload: () => void;
  onView: (change: TemplateChange) => void;
}

const when = (iso: string | null | undefined) => (iso ? inZone(iso, TIMEZONE).format("M/D HH:mm") : "—");

/**
 * 予定テンプレートの画面の「変更依頼」の欄（Level 2、D.5）。選んでいる業務の承認待ちの下書き（新しいテンプレートの下書きを含む）と、
 * 自分の下書きのうち 30 日以内に決まったもの（差し戻しの理由つき）。何も無ければ出さない。
 */
export function ChangeRequestsPanel({ data, loading, error, onReload, onView }: Props) {
  if (error) {
    return (
      <Alert type="warning" showIcon message={`変更依頼を取得できませんでした: ${error.message}`}
        action={<Button size="small" onClick={onReload}>再読み込み</Button>} />
    );
  }
  const rows = data ? [...data.pending, ...data.recentMine] : [];
  if (rows.length === 0) return null;

  return (
    <section className="change-requests" aria-label="変更依頼">
      <Typography.Text strong>変更依頼</Typography.Text>
      <Typography.Text type="secondary">
        {" "}承認待ち {data?.pending.length ?? 0} 件
        {data && data.recentMine.length > 0 && ` ・ 最近決まった自分の下書き ${data.recentMine.length} 件`}
      </Typography.Text>
      <Table<TemplateChange>
        rowKey="id"
        size="small"
        loading={loading}
        // 承認待ちは上に並ぶ（古い順）。多いときだけページに分ける
        pagination={rows.length > 10 ? { pageSize: 10, size: "small", hideOnSinglePage: true } : false}
        dataSource={rows}
        columns={[
          { title: "種類", dataIndex: "kind", width: 96, render: (k: TemplateChange["kind"]) => <Tag>{CHANGE_KIND_LABEL[k]}</Tag> },
          { title: "テンプレート", key: "template", render: (_, c) => c.template_name ?? `#${c.template_id}` },
          {
            title: "状態",
            dataIndex: "status",
            width: 104,
            render: (s: TemplateChange["status"]) => <Tag color={CHANGE_STATUS_COLOR[s]}>{CHANGE_STATUS_LABEL[s]}</Tag>,
          },
          { title: "書いた人", key: "drafter", width: 140, render: (_, c) => c.drafted_by_name ?? `職員 #${c.drafted_by_id}` },
          {
            title: "日時",
            key: "when",
            width: 150,
            className: "mono",
            render: (_, c) => (c.status === "draft" ? `${when(c.updated_at)} 書いた` : `${when(c.decided_at)} 決定`),
          },
          {
            title: "差し戻しの理由",
            key: "note",
            ellipsis: true,
            render: (_, c) => (c.status === "rejected" ? <Typography.Text type="danger">{c.decision_note}</Typography.Text> : null),
          },
          {
            title: "",
            key: "view",
            width: 88,
            render: (_, c) => (
              <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => onView(c)}>
                見る
              </Button>
            ),
          },
        ]}
      />
    </section>
  );
}
