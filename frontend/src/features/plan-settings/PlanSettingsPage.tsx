import { Alert, Button, Empty, InputNumber, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePlanTemplates } from "../../domain/plan/hooks/usePlanTemplates";
import { describeTemplate, POLICY_LABEL, TEMPLATE_TYPE_LABEL } from "../../domain/plan/lib/describe";
import type { PlanTemplate } from "../../domain/plan/types";
import { chipStyle } from "../calendar/lib/chipStyle";

const columns: ColumnsType<PlanTemplate> = [
  { title: "ID", dataIndex: "id", width: 72, className: "mono" },
  {
    title: "予定名",
    dataIndex: "name",
    render: (name: string, t) => {
      const s = chipStyle(t.ui_metadata);
      return (
        <span className="template-name">
          <span className="swatch" style={{ background: s.background }} aria-hidden="true" />
          {name}
        </span>
      );
    },
  },
  { title: "種類", dataIndex: "template_type", render: (type: PlanTemplate["template_type"]) => <Tag>{TEMPLATE_TYPE_LABEL[type]}</Tag> },
  { title: "ルール", key: "rule", render: (_, t) => describeTemplate(t) },
  {
    title: "時間",
    key: "time",
    className: "mono",
    render: (_, t) => `${t.start_time.slice(0, 5)}〜 ${t.duration_minutes}分`,
  },
  { title: "休日の扱い", dataIndex: "holiday_policy", render: (p: PlanTemplate["holiday_policy"]) => POLICY_LABEL[p] },
  { title: "改訂", dataIndex: "revision", width: 72, className: "mono" },
];

/**
 * 予定テンプレートの設定（事前設定）画面。
 * 現段階では参照のみ: 業務IDを指定して、その業務のテンプレート一覧を表示する。
 */
export function PlanSettingsPage() {
  const [params, setParams] = useSearchParams();
  const parsed = Number(params.get("biz"));
  const bizId = Number.isInteger(parsed) && parsed > 0 ? parsed : null;

  const [draft, setDraft] = useState<number | null>(bizId);
  useEffect(() => setDraft(bizId), [bizId]);

  const { data, loading, error, reload } = usePlanTemplates(bizId);

  const show = () => {
    if (draft) setParams({ biz: String(draft) });
  };

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>予定テンプレート</h1>
        </div>
      </header>

      <Space wrap>
        <label className="field">
          <span className="field-label">業務ID</span>
          <InputNumber
            id="biz-id"
            min={1}
            value={draft}
            onChange={(v) => setDraft(typeof v === "number" ? v : null)}
            onPressEnter={show}
            style={{ width: 120 }}
          />
        </label>
        <Button type="primary" onClick={show} disabled={!draft}>
          表示
        </Button>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message={error.message}
          action={error.status !== 404 ? <Button size="small" onClick={reload}>再読み込み</Button> : undefined}
        />
      )}

      {bizId === null ? (
        <Empty description="業務IDを入力してください" />
      ) : (
        <>
          <Typography.Text type="secondary">
            業務ID {bizId} のテンプレート {data ? `${data.length} 件` : ""}
          </Typography.Text>
          <Table<PlanTemplate>
            rowKey="id"
            columns={columns}
            dataSource={data ?? []}
            loading={loading}
            pagination={false}
            size="middle"
            scroll={{ x: 760 }}
            locale={{ emptyText: <Empty description="この業務にはテンプレートがありません" /> }}
          />
        </>
      )}
    </div>
  );
}
