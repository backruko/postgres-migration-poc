import {
  ArrowDownOutlined, ArrowUpOutlined, CheckOutlined, DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, RollbackOutlined,
  SendOutlined, UndoOutlined,
} from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { sopApi } from "../../domain/biz/api/sopApi";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import { bizOptions } from "../../domain/biz/lib/businesses";
import {
  approveBlockedReason, canDecideSop, canDraftSop, canSubmitSop, canWithdrawSop, conditionText, defaultVersion, hasNumberGaps,
  isSubmitted, movedStepIds, SOP_STATE_COLOR, SOP_STATE_LABEL, submitBlockedReason,
} from "../../domain/biz/lib/sop";
import { periodText } from "../../domain/biz/lib/sopVersions";
import type { SopStep, SopVersion } from "../../domain/biz/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { TIMEZONE } from "../../shared/config";
import { ApiError } from "../../shared/api/http";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { inZone } from "../../shared/lib/datetime";
import { ReturnLink } from "../../shared/ui/ReturnLink";
import { ApproveModal } from "./ApproveModal";
import { NewDraftModal } from "./NewDraftModal";
import { RejectDraftModal } from "./RejectDraftModal";
import { RenameDraftModal } from "./RenameDraftModal";
import { SopHistoryDrawer } from "./SopHistoryDrawer";
import { StepFormModal } from "./StepFormModal";

const formatDate = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D");
const formatTime = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm");
const staffName = (name: string | null | undefined, id: number | null | undefined) => name ?? (id != null ? `職員 #${id}` : null);

/**
 * SOP（作業手順）の画面（業務ごと）。版の一覧（草稿も含む）と、選んだ版のステップ。
 * 見るのは誰でも。草稿を作る・直す・削除するのはリリース担当と業務管理者（P.4）。承認済みの版は変えられない。
 * リリース担当は草稿の承認を依頼し（依頼中は誰も直せない。依頼した本人か業務管理者が取り下げられる）、業務管理者が
 * 承認するか理由を付けて差し戻す。業務管理者は依頼の無い草稿もそのまま承認できる（バックエンドが判定する）。
 * 草稿は、この画面でタグ・ステップ（追加・編集・削除・並べ替え）を変えられる（M2.5）。
 * 草稿の承認は発効日と影響（どのオーダーが新しい版を使うか）を見てから。予定の版は発効日の前なら取り消せる（M2.6）。
 * 選んだ業務と版は URL の ?biz= / ?version= に保持する。
 */
export function SopSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const bizId = Number(params.get("biz")) || null;
  const versionParam = Number(params.get("version")) || null;
  const me = useCurrentStaff();
  const canDraft = canDraftSop(me.data);
  const canDecide = canDecideSop(me.data);

  const bizes = useBizMasters();
  const options = useMemo(() => bizOptions(bizes.data ?? []), [bizes.data]);
  const selectedBiz = bizes.data?.find((b) => b.id === bizId);

  const versions = useApiQuery((signal) => sopApi.listVersions(bizId as number, true, signal), [bizId], { enabled: bizId !== null });
  const rows = versions.data ?? [];
  const versionId = versionParam !== null && rows.some((v) => v.id === versionParam) ? versionParam : (defaultVersion(rows)?.id ?? null);
  const detail = useApiQuery((signal) => sopApi.getVersion(versionId as number, signal), [versionId], { enabled: versionId !== null });

  const [newDraftOpen, setNewDraftOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  // ステップの追加・編集モーダル（step が null なら追加）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [stepEditor, setStepEditor] = useState<{ open: boolean; step: SopStep | null }>({ open: false, step: null });
  const [moving, setMoving] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const steps = detail.data?.id === versionId ? detail.data.steps : [];

  const reorder = async (ids: number[], done: string) => {
    if (versionId === null) return;
    setMoving(true);
    try {
      await sopApi.reorderSteps(versionId, ids);
      message.success(done);
    } catch (e) {
      message.error(`並べ替えできませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
    } finally {
      setMoving(false);
    }
    detail.reload();
  };

  const confirmDeleteStep = (step: SopStep) =>
    modal.confirm({
      title: `ステップ ${step.step_num}「${step.title}」を削除しますか？`,
      content: "残りのステップの番号はそのままです（「番号を振り直す」で 1 から並べ直せます）。",
      okText: "削除",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sopApi.deleteStep(step.id);
          message.success(`ステップ ${step.step_num} を削除しました。`);
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        detail.reload();
      },
    });

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
      key: "state",
      width: 96,
      render: (_, v) => (isSubmitted(v) ? <Tag color="gold">承認待ち</Tag> : <Tag color={SOP_STATE_COLOR[v.state]}>{SOP_STATE_LABEL[v.state]}</Tag>),
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
  const submitted = selected !== null && isSubmitted(selected);
  // 直せるのは、依頼していない草稿だけ（依頼中は誰も直せない）
  const editable = canDraft && selected?.state === "draft" && !submitted;
  const currentVersion = rows.find((v) => v.state === "current") ?? null;
  const stepsLoaded = selected !== null && detail.data?.id === selected.id;
  const approveBlocked = selected && canDecide && selected.state === "draft" && stepsLoaded ? approveBlockedReason(selected, rows, steps.length) : null;
  const submitBlocked = stepsLoaded ? submitBlockedReason(steps.length) : null;

  const afterDecision = () => {
    versions.reload();
    detail.reload();
  };

  const confirmSubmit = (v: SopVersion) =>
    modal.confirm({
      title: `${v.version_tag} の承認を依頼しますか？`,
      content: "業務管理者が承認するか差し戻すまで、この草稿は誰も直せません（取り下げると、また直せます）。",
      okText: "承認を依頼",
      cancelText: "キャンセル",
      onOk: async () => {
        setBusy(true);
        try {
          await sopApi.submit(v.id);
          message.success(`${v.version_tag} の承認を依頼しました。業務管理者の判断を待ちます。`);
        } catch (e) {
          message.error(`依頼できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        } finally {
          setBusy(false);
        }
        afterDecision();
      },
    });

  const confirmWithdraw = (v: SopVersion) =>
    modal.confirm({
      title: `${v.version_tag} の承認の依頼を取り下げますか？`,
      content: "草稿に戻り、また直せるようになります。",
      okText: "取り下げる",
      cancelText: "キャンセル",
      onOk: async () => {
        setBusy(true);
        try {
          await sopApi.withdraw(v.id);
          message.success(`${v.version_tag} の依頼を取り下げました。`);
        } catch (e) {
          message.error(`取り下げられませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        } finally {
          setBusy(false);
        }
        afterDecision();
      },
    });

  const confirmCancelApproval = (v: SopVersion) =>
    modal.confirm({
      title: `${v.version_tag} の承認を取り消しますか？`,
      content: "草稿に戻り、編集できるようになります。今有効な版は、終わりの無い版に戻ります。オーダーが指定している版は取り消せません。",
      okText: "承認を取り消す",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sopApi.cancelApproval(bizId as number, v.id);
          message.success(`${v.version_tag} を草稿に戻しました。`);
        } catch (e) {
          message.error(`取り消せませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`, 8);
        }
        versions.reload();
        detail.reload();
      },
    });
  const columns: ColumnsType<SopStep> = editable
    ? [
        ...stepColumns,
        {
          title: "操作",
          key: "actions",
          width: 200,
          render: (_, step, index) => (
            <Space size={0}>
              <Button type="text" size="small" icon={<ArrowUpOutlined />} aria-label="上へ" title="上へ" disabled={moving || index === 0}
                onClick={() => void reorder(movedStepIds(steps, index, -1)!, `ステップ「${step.title}」を上へ動かしました。`)} />
              <Button type="text" size="small" icon={<ArrowDownOutlined />} aria-label="下へ" title="下へ" disabled={moving || index === steps.length - 1}
                onClick={() => void reorder(movedStepIds(steps, index, 1)!, `ステップ「${step.title}」を下へ動かしました。`)} />
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setStepEditor({ open: true, step })}>編集</Button>
              <Button type="link" size="small" danger onClick={() => confirmDeleteStep(step)}>削除</Button>
            </Space>
          ),
        },
      ]
    : stepColumns;

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
                  版 {rows.length} 件{!canDraft && " ・ 変更はリリース担当・業務管理者ができます"}
                </Typography.Text>
                {canDraft && (
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
                <Empty description={rows.length ? "版を選んでください" : canDraft ? "「新しい版」で最初の草稿を作ってください" : "まだ版がありません"} />
              ) : (
                <>
                  <div className="table-head">
                    <Space size={8} wrap>
                      <Typography.Title level={4} style={{ margin: 0 }} className="mono">{selected.version_tag}</Typography.Title>
                      <Tag color={SOP_STATE_COLOR[selected.state]}>{SOP_STATE_LABEL[selected.state]}</Tag>
                      {submitted && <Tag color="gold">承認待ち</Tag>}
                      <span className="muted mono">{periodText(selected, formatDate)}</span>
                    </Space>
                    <Space size={8} wrap>
                      {editable && (
                        <>
                          <Button size="small" onClick={() => setRenameOpen(true)}>タグを変更</Button>
                          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setStepEditor({ open: true, step: null })}>
                            ステップを追加
                          </Button>
                          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDeleteDraft(selected)}>
                            草稿を削除
                          </Button>
                        </>
                      )}
                      {editable && canSubmitSop(selected, me.data) && (
                        <Button
                          size="small"
                          type="primary"
                          icon={<SendOutlined />}
                          loading={busy}
                          disabled={submitBlocked !== null || !stepsLoaded}
                          title={submitBlocked ?? undefined}
                          onClick={() => confirmSubmit(selected)}
                        >
                          承認を依頼
                        </Button>
                      )}
                      {submitted && canWithdrawSop(selected, me.data) && (
                        <Button size="small" icon={<RollbackOutlined />} loading={busy} onClick={() => confirmWithdraw(selected)}>
                          取り下げる
                        </Button>
                      )}
                      {submitted && canDecide && (
                        <Button size="small" danger onClick={() => setRejectOpen(true)}>差し戻す…</Button>
                      )}
                      {canDecide && selected.state === "draft" && (
                        <Button
                          size="small"
                          type="primary"
                          icon={<CheckOutlined />}
                          disabled={approveBlocked !== null || !stepsLoaded}
                          title={approveBlocked ?? undefined}
                          onClick={() => setApproveOpen(true)}
                        >
                          承認…
                        </Button>
                      )}
                      {canDecide && selected.state === "scheduled" && (
                        <Button size="small" danger icon={<UndoOutlined />} onClick={() => confirmCancelApproval(selected)}>
                          予定を取り消す
                        </Button>
                      )}
                      <Button size="small" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>履歴</Button>
                    </Space>
                  </div>
                  <Typography.Paragraph type="secondary" className="sop-authors">
                    {[
                      staffName(selected.created_by_name, selected.created_by_id) && `作成 ${staffName(selected.created_by_name, selected.created_by_id)}`,
                      selected.updated_at && `最終編集 ${staffName(selected.updated_by_name, selected.updated_by_id) ?? "—"} ${formatTime(selected.updated_at)}`,
                    ].filter(Boolean).join(" ・ ") || "作成・編集の記録はありません（2026-11-11 より前の版）"}
                  </Typography.Paragraph>
                  {submitted && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message={`${staffName(selected.submitted_by_name, selected.submitted_by_id)} が ${formatTime(selected.submitted_at!)} に承認を依頼しました。`}
                      description={
                        canDecide
                          ? "内容を確かめて、承認するか、理由を付けて差し戻してください。依頼中は誰も直せません。"
                          : "業務管理者が承認するか差し戻すまで、誰も直せません。直すときは取り下げてください（依頼した本人か業務管理者）。"
                      }
                    />
                  )}
                  {selected.state !== "draft" && (
                    <Typography.Paragraph type="secondary">承認済みの版は変えられません。変えるときは「新しい版」で草稿を作ります。</Typography.Paragraph>
                  )}
                  {approveBlocked && <Typography.Paragraph type="secondary">承認できません: {approveBlocked}</Typography.Paragraph>}
                  {editable && hasNumberGaps(steps) && (
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="ステップの番号に抜けがあります（オーダーには番号のまま出ます）。"
                      action={<Button size="small" disabled={moving} onClick={() => void reorder(steps.map((x) => x.id), "番号を 1 から振り直しました。")}>番号を振り直す</Button>}
                    />
                  )}
                  {detail.error && <Alert type="error" showIcon message={detail.error.message} />}
                  <Table<SopStep>
                    rowKey="id"
                    columns={columns}
                    dataSource={steps}
                    loading={detail.loading || moving}
                    pagination={false}
                    size="middle"
                    locale={{ emptyText: <Empty description="ステップがありません" /> }}
                  />
                </>
              )}
            </section>
          </div>
          {canDraft && versionId !== null && (
            <>
              <StepFormModal
                open={stepEditor.open}
                versionId={versionId}
                step={stepEditor.step}
                onClose={() => setStepEditor((x) => ({ ...x, open: false }))}
                onSaved={detail.reload}
              />
              <RenameDraftModal
                open={renameOpen}
                draft={selected}
                versions={rows}
                onClose={() => setRenameOpen(false)}
                onSaved={versions.reload}
              />
            </>
          )}
          {canDecide && versionId !== null && (
            <>
              <ApproveModal
                open={approveOpen}
                bizId={bizId}
                draft={selected}
                current={currentVersion}
                me={me.data}
                onClose={() => setApproveOpen(false)}
                onApproved={afterDecision}
              />
              <RejectDraftModal open={rejectOpen} draft={selected} onClose={() => setRejectOpen(false)} onRejected={afterDecision} />
            </>
          )}
          <SopHistoryDrawer open={historyOpen} version={selected} onClose={() => setHistoryOpen(false)} />
          {canDraft && (
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
