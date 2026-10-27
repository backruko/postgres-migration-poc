import { PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Select, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import type { BizMaster } from "../../domain/biz/types";
import { usePlanTemplates } from "../../domain/plan/hooks/usePlanTemplates";
import { describeTemplate, POLICY_LABEL, TEMPLATE_TYPE_LABEL } from "../../domain/plan/lib/describe";
import type { PlanTemplate } from "../../domain/plan/types";
import { chipStyle } from "../calendar/lib/chipStyle";
import { CreateTemplateModal } from "./CreateTemplateModal";

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

/** 業務の選択肢（顧客コードごとにグループ化、業務コード順） */
function bizOptions(bizes: BizMaster[]) {
  const byClient = new Map<string, BizMaster[]>();
  for (const b of [...bizes].sort((a, b) => a.business_code.localeCompare(b.business_code))) {
    byClient.set(b.client_code, [...(byClient.get(b.client_code) ?? []), b]);
  }
  return [...byClient.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([clientCode, list]) => ({
      label: `顧客 ${clientCode}`,
      title: clientCode,
      options: list.map((b) => ({ value: b.id, label: `${b.business_code}　${b.name}` })),
    }));
}

/**
 * 予定テンプレートの設定（事前設定）画面。
 * 現段階では参照のみ: 業務を選択して、その業務のテンプレート一覧を表示する。
 * 選択した業務は URL の ?biz= に保持する（再読み込み・共有しても同じ業務を表示）。
 */
export function PlanSettingsPage() {
  const [params, setParams] = useSearchParams();
  const parsed = Number(params.get("biz"));
  const bizId = Number.isInteger(parsed) && parsed > 0 ? parsed : null;

  const bizes = useBizMasters();
  const options = useMemo(() => bizOptions(bizes.data ?? []), [bizes.data]);
  const selectedBiz = bizes.data?.find((b) => b.id === bizId);
  // URL の業務が一覧に無い場合（削除済み・存在しない ID）も、選択中であることが分かるようにする
  const unknownOption = bizId !== null && bizes.data && !selectedBiz ? [{ value: bizId, label: `業務ID ${bizId}` }] : [];

  const { data, loading, error, reload } = usePlanTemplates(bizId);
  const [creating, setCreating] = useState(false);

  const selectBiz = (id?: number) => setParams(id ? { biz: String(id) } : {});

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>予定テンプレート</h1>
        </div>
      </header>

      <label className="field">
        <span className="field-label">業務</span>
        <Select<number>
          id="biz"
          showSearch
          allowClear
          placeholder="業務を選択（業務コード・業務名で検索）"
          value={bizId ?? undefined}
          options={[...unknownOption, ...options]}
          optionFilterProp="label"
          loading={bizes.loading}
          onChange={selectBiz}
          notFoundContent={bizes.loading ? "読み込み中…" : "該当する業務がありません"}
          style={{ width: "min(420px, 100%)" }}
        />
      </label>

      {bizes.error && (
        <Alert
          type="warning"
          showIcon
          message={`業務の一覧を取得できませんでした: ${bizes.error.message}`}
          action={<Button size="small" onClick={bizes.reload}>再読み込み</Button>}
        />
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          message={error.message}
          action={error.status !== 404 ? <Button size="small" onClick={reload}>再読み込み</Button> : undefined}
        />
      )}

      {bizId === null ? (
        <Empty description="業務を選択してください" />
      ) : (
        <>
          <div className="table-head">
            <Typography.Text type="secondary">
              {selectedBiz ? `${selectedBiz.business_code} ${selectedBiz.name}` : `業務ID ${bizId}`} のテンプレート
              {data ? ` ${data.length} 件` : ""}
            </Typography.Text>
            {/* 作成先は選択中の業務（一覧に無い業務IDの場合は作成できない） */}
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)} disabled={!selectedBiz}>
              新規作成
            </Button>
          </div>
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
      {selectedBiz && (
        <CreateTemplateModal open={creating} biz={selectedBiz} onClose={() => setCreating(false)} onCreated={reload} />
      )}
    </div>
  );
}
