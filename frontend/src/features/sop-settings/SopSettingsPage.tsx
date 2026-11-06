import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { sopApi } from "../../domain/biz/api/sopApi";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import { bizOptions } from "../../domain/biz/lib/businesses";
import { canManageSop, conditionText, defaultVersion, SOP_STATE_COLOR, SOP_STATE_LABEL } from "../../domain/biz/lib/sop";
import { periodText } from "../../domain/biz/lib/sopVersions";
import type { SopStep, SopVersion } from "../../domain/biz/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { TIMEZONE } from "../../shared/config";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { inZone } from "../../shared/lib/datetime";
import { ReturnLink } from "../../shared/ui/ReturnLink";
import { NewDraftModal } from "./NewDraftModal";

const formatDate = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D");

/**
 * SOP（作業手順）の画面（業務ごと）。版の一覧（草稿も含む）と、選んだ版のステップ。
 * 見るのは誰でも。新しい版・草稿の削除は業務管理者だけ（バックエンドが判定する）。承認済みの版は変えられない。
 * 草稿の編集（M2.5）と承認（M2.6）はこの画面に足していく。
 * 選んだ業務と版は URL の ?biz= / ?version= に保持する。
 */
export function SopSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const bizId = Number(params.get("biz")) || null;
  const versionParam = Number(params.get("version")) || null;
  const me = useCurrentStaff();
  const canManage = canManageSop(me.data);

  const bizes = useBizMasters();
  const options = useMemo(() => bizOptions(bizes.data ?? []), [bizes.data]);
  const selectedBiz = bizes.data?.find((b) => b.id === bizId);

  const versions = useApiQuery((signal) => sopApi.listVersions(bizId as number, true, signal), [bizId], { enabled: bizId !== null });
  const rows = versions.data ?? [];
  const versionId = versionParam !== null && rows.some((v) => v.id === versionParam) ? versionParam : (defaultVersion(rows)?.id ?? null);
  const detail = useApiQuery((signal) => sopApi.getVersion(versionId as number, signal), [versionId], { enabled: versionId !== null });

  const [newDraftOpen, setNewDraftOpen] = useState(false);

  const setQuery = (biz: number | null, version: number | null) => {
    const next = new URLSearchParams(params);
    if (biz) next.set("biz", String(biz));
    else next.delete("biz");
    if (version) next.set("version", String(version));
    else next.delete("version");
    setParams(next, { replace: true });
  };

  const confirmDeleteDraft = (v: SopVersion) =>
    modal.confirm({
      title: `草稿 ${v.version_tag} を削除しますか？`,
      content: "草稿とそのステップを削除します。元に戻せません。",
      okText: "削除",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sopApi.deleteDraft(v.id);
          message.success(`草稿 ${v.version_tag} を削除しました。`);
          setQuery(bizId, null);
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        versions.reload();
      },
    });

  const versionColumns: ColumnsType<SopVersion> = [
    { title: "タグ", dataIndex: "version_tag", className: "mono" },
    {
      title: "状態",
      dataIndex: "state",
      width: 72,
      render: (s: SopVersion["state"]) => <Tag color={SOP_STATE_COLOR[s]}>{SOP_STATE_LABEL[s]}</Tag>,
    },
    { title: "有効期間", key: "period", className: "mono", render: (_, v) => periodText(v, formatDate) },
  ];

  const stepColumns: ColumnsType<SopStep> = [
    { title: "番号", dataIndex: "step_num", width: 64, className: "mono" },
    {
      title: "手順",
      key: "title",
      render: (_, s) => (
        <div>
          <Space size={6}>
            {s.title}
            {s.is_blocking && <Tag color="purple">承認で次へ</Tag>}
          </Space>
          {s.content && <div className="muted sop-step-content">{s.content}</div>}
        </div>
      ),
    },
    { title: "含める条件", dataIndex: "condition", width: 180, render: (c: SopStep["condition"]) => conditionText(c) },
  ];

  const selected = rows.find((v) => v.id === versionId) ?? null;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>SOP（作業手順）</h1>
          <ReturnLink />
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
          options={options}
          optionFilterProp="label"
          loading={bizes.loading}
          onChange={(id?: number) => setQuery(id ?? null, null)}
          notFoundContent={bizes.loading ? "読み込み中…" : "該当する業務がありません"}
          style={{ width: "min(420px, 100%)" }}
        />
      </label>

      {versions.error && (
        <Alert type="error" showIcon message={versions.error.message} action={<Button size="small" onClick={versions.reload}>再読み込み</Button>} />
      )}

      {bizId === null ? (
        <Empty description="業務を選択してください" />
      ) : (
        <>
          {selectedBiz?.execution_type === "lightweight" && (
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message="この業務は顧客側の出来事（ステップなし）のため、オーダーは SOP を使いません。" />
          )}
          <div className="sop-layout">
            <section>
              <div className="table-head">
                <Typography.Text type="secondary">
                  版 {rows.length} 件{!canManage && " ・ 変更は業務管理者だけができます"}
                </Typography.Text>
                {canManage && (
                  <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setNewDraftOpen(true)} disabled={!versions.data}>
                    新しい版
                  </Button>
                )}
              </div>
              <Table<SopVersion>
                rowKey="id"
                columns={versionColumns}
                dataSource={rows}
                loading={versions.loading}
                pagination={false}
                size="small"
                onRow={(v) => ({ onClick: () => setQuery(bizId, v.id), style: { cursor: "pointer" } })}
                rowClassName={(v) => [v.id === versionId ? "sop-version-selected" : "", v.state === "past" ? "is-past-row" : ""].join(" ")}
                locale={{ emptyText: <Empty description="版がありません" /> }}
              />
            </section>

            <section>
              {selected === null ? (
                <Empty description={rows.length ? "版を選んでください" : canManage ? "「新しい版」で最初の草稿を作ってください" : "まだ版がありません"} />
              ) : (
                <>
                  <div className="table-head">
                    <Space size={8} wrap>
                      <Typography.Title level={4} style={{ margin: 0 }} className="mono">{selected.version_tag}</Typography.Title>
                      <Tag color={SOP_STATE_COLOR[selected.state]}>{SOP_STATE_LABEL[selected.state]}</Tag>
                      <span className="muted mono">{periodText(selected, formatDate)}</span>
                    </Space>
                    {canManage && selected.state === "draft" && (
                      <Button size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDeleteDraft(selected)}>
                        草稿を削除
                      </Button>
                    )}
                  </div>
                  {selected.state !== "draft" && (
                    <Typography.Paragraph type="secondary">承認済みの版は変えられません。変えるときは「新しい版」で草稿を作ります。</Typography.Paragraph>
                  )}
                  {detail.error && <Alert type="error" showIcon message={detail.error.message} />}
                  <Table<SopStep>
                    rowKey="id"
                    columns={stepColumns}
                    dataSource={detail.data?.id === selected.id ? detail.data.steps : []}
                    loading={detail.loading}
                    pagination={false}
                    size="middle"
                    locale={{ emptyText: <Empty description="ステップがありません" /> }}
                  />
                </>
              )}
            </section>
          </div>
          {canManage && (
            <NewDraftModal
              open={newDraftOpen}
              bizId={bizId}
              versions={rows}
              onClose={() => setNewDraftOpen(false)}
              onCreated={(v) => {
                versions.reload();
                setQuery(bizId, v.id);
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
