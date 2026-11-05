import { PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Form, Modal, Radio, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { dependencyApi } from "../../domain/biz/api/dependencyApi";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import {
  canManageDependencies, KIND_HELP, KIND_LABEL, MATCH_RULE_HELP, MATCH_RULE_LABEL,
} from "../../domain/biz/lib/dependencies";
import type { BizDependency, BizDependencyCreate, DependencyKind, DependencyMatchRule } from "../../domain/biz/types";
import { useClients } from "../../domain/client/hooks/useClients";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { dayjs } from "../../shared/lib/datetime";

interface FormValues {
  upstream_biz_id?: number;
  downstream_biz_id?: number;
  match_rule: DependencyMatchRule;
  kind: DependencyKind;
}

/**
 * 業務の依存関係の設定画面。顧客を選び、その業務どうしの依存関係（上流 → 下流）を一覧・追加・削除する。
 * 作成・削除は業務管理者だけ（バックエンドが判定する）。定義は同じ顧客の業務どうしのみ。
 * 定義を作ると下流の業務の開始前のオーダーに上流のオーダーとのつながりができ、hard なら上流の完了まで開始できない。
 * 選択した顧客は URL の ?client= に保持する。
 */
export function DependencySettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const clientCode = params.get("client")?.toUpperCase() || null;
  const clients = useClients();
  const bizes = useBizMasters();
  const me = useCurrentStaff();
  const canManage = canManageDependencies(me.data);
  const deps = useApiQuery(
    (signal) => dependencyApi.listForClient(clientCode as string, signal), [clientCode], { enabled: clientCode !== null },
  );

  const [form] = Form.useForm<FormValues>();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const clientOptions = (clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }));
  const bizOptions = useMemo(
    () => (bizes.data ?? []).filter((b) => b.client_code === clientCode).map((b) => ({ value: b.id, label: `${b.business_code} ${b.name}` })),
    [bizes.data, clientCode],
  );

  const openForm = () => {
    form.resetFields();
    form.setFieldsValue({ match_rule: "same_sheet_event", kind: "hard" });
    setError(null);
    setOpen(true);
  };

  const save = async (v: FormValues) => {
    if (v.upstream_biz_id === undefined || v.downstream_biz_id === undefined) return;
    setSaving(true);
    setError(null);
    try {
      const body: BizDependencyCreate = { upstream_biz_id: v.upstream_biz_id, match_rule: v.match_rule, kind: v.kind };
      const created = await dependencyApi.create(v.downstream_biz_id, body);
      message.success(`${created.downstream.business_code} が ${created.upstream.business_code} を待つ依存関係を追加しました`);
      setOpen(false);
      deps.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "追加できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = (d: BizDependency) =>
    modal.confirm({
      title: `${d.downstream.business_code} → ${d.upstream.business_code} の依存関係を削除しますか？`,
      content: "オーダーのつながりは「外れた」として記録に残り、開始を止めなくなります。",
      okText: "削除",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await dependencyApi.remove(d.id);
          message.success("依存関係を削除しました");
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        deps.reload();
      },
    });

  const biz = (b: BizDependency["upstream"]) => (
    <span><span className="mono">{b.business_code}</span> {b.name}</span>
  );
  const columns: ColumnsType<BizDependency> = [
    { title: "上流（待たれる業務）", key: "upstream", render: (_, d) => biz(d.upstream) },
    { title: "", key: "arrow", width: 32, render: () => <span className="muted">→</span> },
    { title: "下流（待つ業務）", key: "downstream", render: (_, d) => biz(d.downstream) },
    { title: "突き合わせ", dataIndex: "match_rule", render: (v: DependencyMatchRule) => <span title={MATCH_RULE_HELP[v]}>{MATCH_RULE_LABEL[v]}</span> },
    {
      title: "種類",
      dataIndex: "kind",
      width: 88,
      render: (v: DependencyKind) => <Tag color={v === "hard" ? "orange" : undefined} title={KIND_HELP[v]}>{KIND_LABEL[v]}</Tag>,
    },
    { title: "登録日", dataIndex: "created_at", width: 112, className: "mono", render: (v: string) => dayjs(v).format("YYYY/M/D") },
    ...(canManage
      ? [{
          title: "操作",
          key: "actions",
          width: 80,
          render: (_: unknown, d: BizDependency) => (
            <Button type="link" size="small" danger onClick={() => confirmDelete(d)}>削除</Button>
          ),
        }]
      : []),
  ];

  const rows = deps.data ?? [];

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>業務の依存関係</h1>
        </div>
      </header>

      <label className="field">
        <span className="field-label">顧客</span>
        <Select<string>
          id="client"
          showSearch
          allowClear
          placeholder="顧客を選択（顧客コード・顧客名で検索）"
          value={clientCode ?? undefined}
          options={clientOptions}
          optionFilterProp="label"
          loading={clients.loading}
          onChange={(code?: string) => setParams(code ? { client: code } : {})}
          notFoundContent={clients.loading ? "読み込み中…" : "該当する顧客がありません"}
          style={{ width: "min(420px, 100%)" }}
        />
      </label>

      {deps.error && (
        <Alert type="error" showIcon message={deps.error.message} action={<Button size="small" onClick={deps.reload}>再読み込み</Button>} />
      )}

      {clientCode === null ? (
        <Empty description="顧客を選択してください" />
      ) : (
        <>
          <div className="table-head">
            <Typography.Text type="secondary">
              依存関係 {rows.length} 件 ・ 「待つ」は上流のオーダーが完了するまで下流のオーダーを開始できません
              {!canManage && " ・ 追加・削除は業務管理者だけができます"}
            </Typography.Text>
            {canManage && (
              <Button type="primary" icon={<PlusOutlined />} onClick={openForm} disabled={bizOptions.length < 2}>
                依存関係を追加
              </Button>
            )}
          </div>
          <Table<BizDependency>
            rowKey="id"
            columns={columns}
            dataSource={rows}
            loading={deps.loading}
            pagination={false}
            size="middle"
            scroll={{ x: 880 }}
            locale={{ emptyText: <Empty description="依存関係はありません" /> }}
          />
        </>
      )}

      <Modal
        open={open}
        title="依存関係を追加"
        okText="追加"
        cancelText="キャンセル"
        onOk={() => form.submit()}
        onCancel={() => setOpen(false)}
        confirmLoading={saving}
        destroyOnClose
      >
        {error && <Alert type="error" showIcon message={error} className="modal-alert" />}
        <Form form={form} layout="vertical" onFinish={save} disabled={saving}>
          <Form.Item name="upstream_biz_id" label="上流（先に終わる業務）" rules={[{ required: true, message: "上流の業務を選んでください" }]}>
            <Select options={bizOptions} showSearch optionFilterProp="label" placeholder="業務を選択" />
          </Form.Item>
          <Form.Item
            name="downstream_biz_id"
            label="下流（上流の完了を待つ業務）"
            dependencies={["upstream_biz_id"]}
            rules={[
              { required: true, message: "下流の業務を選んでください" },
              ({ getFieldValue }) => ({
                validator: (_, v) => (v !== undefined && v === getFieldValue("upstream_biz_id")
                  ? Promise.reject(new Error("上流と別の業務を選んでください")) : Promise.resolve()),
              }),
            ]}
          >
            <Select options={bizOptions} showSearch optionFilterProp="label" placeholder="業務を選択" />
          </Form.Item>
          <Form.Item name="match_rule" label="突き合わせ（下流のオーダーがどの上流のオーダーを待つか）">
            <Radio.Group>
              <Space direction="vertical">
                {/* 直前の上流（latest_before）は日数の入力と一緒に L.3 で選べるようにする */}
                {(Object.keys(MATCH_RULE_LABEL) as DependencyMatchRule[]).filter((r) => r !== "latest_before").map((r) => (
                  <Radio key={r} value={r}>{MATCH_RULE_LABEL[r]} <span className="muted">— {MATCH_RULE_HELP[r]}</span></Radio>
                ))}
              </Space>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="kind" label="種類">
            <Radio.Group>
              <Space direction="vertical">
                {(Object.keys(KIND_LABEL) as DependencyKind[]).map((k) => (
                  <Radio key={k} value={k}>{KIND_LABEL[k]} <span className="muted">— {KIND_HELP[k]}</span></Radio>
                ))}
              </Space>
            </Radio.Group>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
