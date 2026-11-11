import { DeleteOutlined, EditOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useBizMasters } from "../../domain/biz/hooks/useBizMasters";
import { bizOptions } from "../../domain/biz/lib/businesses";
import { BizSettingsPanel } from "./BizSettingsPanel";
import { canEditPlanSettings, PLAN_SETTINGS_ADMIN_ONLY } from "../../domain/biz/lib/settings";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { templateApi, templateChangeApi } from "../../domain/plan/api";
import { usePlanTemplates } from "../../domain/plan/hooks/usePlanTemplates";
import { useBizChangeRequests } from "../../domain/plan/hooks/useTemplateChanges";
import { DRAFT_SAVED_MESSAGE, draftErrorText, pendingDraftReason, templateWriteMode } from "../../domain/plan/lib/templateChanges";
import { CATEGORY_LABEL, categoryOf, type TemplateCategory } from "../../domain/plan/lib/category";
import { describeDeadline } from "../../domain/plan/lib/deadline";
import { describeTemplate, POLICY_LABEL, TEMPLATE_TYPE_LABEL } from "../../domain/plan/lib/describe";
import type { PlanTemplate, TemplateChange } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";
import { ReturnLink } from "../../shared/ui/ReturnLink";
import { chipStyle } from "../calendar/lib/chipStyle";
import { ChangeRequestsPanel } from "./ChangeRequestsPanel";
import { DraftViewModal } from "./DraftViewModal";
import { deleteDraftBody } from "./templateDraft";
import { TemplateFormModal } from "./TemplateFormModal";

const CATEGORY_COLOR: Record<TemplateCategory, string | undefined> = { regular: undefined, adhoc: "gold", special: "red" };

/** 下書きを書く人（リリース担当）に見せる案内 */
const DRAFT_MODE_NOTE = "変更は下書きとして保存し、業務管理者が承認します";

const NO_UPCOMING_HINT =
  "2年以内に予定がありません。ルール・指定日・参照するシートの日付を確認してください（業務が終わった場合は削除できます）。";

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
  {
    title: "分類",
    key: "category",
    width: 104,
    render: (_, t) => {
      const c = categoryOf(t.ui_metadata);
      return <Tag color={CATEGORY_COLOR[c]}>{CATEGORY_LABEL[c]}</Tag>;
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
  { title: "締切", key: "deadline", render: (_: unknown, t: PlanTemplate) => describeDeadline(t) },
  {
    title: "次の予定",
    key: "next",
    width: 168,
    // 次の予定は、テンプレートのタイムゾーンでの日時で表示する（2年以内に無い場合は警告）
    render: (_, t) =>
      t.next_occurrence_at ? (
        <span className="mono">{inZone(t.next_occurrence_at, t.timezone).format("YYYY/M/D(dd) HH:mm")}</span>
      ) : (
        <Tag color="warning" title={NO_UPCOMING_HINT}>
          今後の予定がありません
        </Tag>
      ),
  },
  { title: "改訂", dataIndex: "revision", width: 72, className: "mono" },
];

/**
 * 予定テンプレートの設定（事前設定）画面。
 * 業務を選択して、その業務のテンプレートを一覧・作成・編集・削除する。
 * 選択した業務は URL の ?biz= に保持する（再読み込み・共有しても同じ業務を表示）。
 */
export function PlanSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const parsed = Number(params.get("biz"));
  const bizId = Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  // ?template= は日次ダッシュボードからのリンク。その行を強調し、見える位置までスクロールする
  const parsedTemplate = Number(params.get("template"));
  const focusTemplateId = Number.isInteger(parsedTemplate) && parsedTemplate > 0 ? parsedTemplate : null;

  // 業務の設定の編集は業務管理者だけ。テンプレートは業務管理者が直接、リリース担当は下書きで変える（Level 2。見るのは誰でも）
  const me = useCurrentStaff();
  const canEdit = canEditPlanSettings(me.data);
  const writeMode = templateWriteMode(me.data);

  const bizes = useBizMasters();
  const options = useMemo(() => bizOptions(bizes.data ?? []), [bizes.data]);
  const selectedBiz = bizes.data?.find((b) => b.id === bizId);
  // URL の業務が一覧に無い場合（削除済み・存在しない ID）も、選択中であることが分かるようにする
  const unknownOption = bizId !== null && bizes.data && !selectedBiz ? [{ value: bizId, label: `業務ID ${bizId}` }] : [];

  const { data, loading, error, reload } = usePlanTemplates(bizId);
  const changes = useBizChangeRequests(bizId, me.data?.id ?? null);
  const reloadAll = () => {
    reload();
    changes.reload();
  };
  const pendingOf = useMemo(
    () => new Map((changes.data?.pending ?? []).filter((c) => c.template_id !== null).map((c) => [c.template_id as number, c])),
    [changes.data],
  );
  const withoutUpcoming = (data ?? []).filter((t) => !t.next_occurrence_at).length;
  const focusLoaded = focusTemplateId !== null && (data ?? []).some((t) => t.id === focusTemplateId);
  useEffect(() => {
    if (!focusLoaded) return;
    document.querySelector(`.template-row-focus`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusLoaded, focusTemplateId]);
  // 作成・編集モーダル（template が null なら新規作成。draft は直す下書き）。閉じる間の表示が切り替わらないよう、閉じても残す
  const [editor, setEditor] = useState<{ open: boolean; template: PlanTemplate | null; draft: TemplateChange | null }>({
    open: false, template: null, draft: null,
  });
  const openEditor = (template: PlanTemplate | null, draft: TemplateChange | null = null) => setEditor({ open: true, template, draft });

  // 下書きを見る窓
  const [viewer, setViewer] = useState<{ open: boolean; change: TemplateChange | null }>({ open: false, change: null });
  const openViewer = (change: TemplateChange) => setViewer({ open: true, change });
  /** 一覧の行の「下書きを見る」: 読み込み済みの承認待ちに無ければ（読み込みのすれ違い）、その 1 件を読む */
  const viewPending = async (t: PlanTemplate) => {
    const loaded = pendingOf.get(t.id);
    if (loaded) return openViewer(loaded);
    if (t.pending_change_id == null) return;
    try {
      openViewer(await templateChangeApi.get(t.pending_change_id));
    } catch (e) {
      message.error(`下書きを読めませんでした: ${draftErrorText(e)}`);
    }
  };
  const templateOf = (c: TemplateChange | null) =>
    c?.template_id != null ? (data ?? []).find((t) => t.id === c.template_id) ?? null : null;
  /** 窓の「直す」: 下書きの中身でフォームを開く（変更の下書きは、対象のテンプレートの今の値が要る） */
  const editDraft = (c: TemplateChange) => {
    const t = templateOf(c);
    if (c.kind === "update" && !t) {
      message.error("対象のテンプレートが見つかりません。画面を読み直してください。");
      return;
    }
    setViewer((v) => ({ ...v, open: false }));
    openEditor(t, c);
  };

  // リリース担当の「削除を依頼」: 削除の下書きを作る（承認されるまで、テンプレートも予定もそのまま）
  const confirmDeleteRequest = (t: PlanTemplate) =>
    modal.confirm({
      title: `「${t.name}」の削除を依頼しますか？`,
      content: "削除の下書きを作ります。業務管理者が承認すると、このテンプレートと未来の予定が削除されます。承認されるまでは今のままです。",
      okText: "削除を依頼する",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await templateChangeApi.create(deleteDraftBody(t));
          message.success(DRAFT_SAVED_MESSAGE);
        } catch (e) {
          message.error(`依頼できませんでした: ${draftErrorText(e)}`, 8);
        }
        reloadAll();
      },
    });

  // 削除は確認してから。削除中は確認ダイアログの OK ボタンが読み込み中になり、終わると閉じる
  const confirmDelete = (t: PlanTemplate) =>
    modal.confirm({
      title: `「${t.name}」を削除しますか？`,
      content: "本当にこのテンプレートを削除しますか？この操作により、未来の予定もすべて削除されます。",
      okText: "削除する",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await templateApi.delete(t.id);
          message.success("テンプレートを削除しました");
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError && e.code !== "TEMPLATE_DRAFT_PENDING" ? e.message : draftErrorText(e)}`);
        }
        // 失敗した場合も一覧を読み直す（別の画面で削除済みなど、一覧が古い可能性がある）
        reload();
      },
    });

  /** 予定名の列: 承認待ちの下書きがあれば「承認待ち」（だれが・いつ） */
  const nameCell = (name: string, t: PlanTemplate) => {
    const s = chipStyle(t.ui_metadata);
    const p = pendingOf.get(t.id);
    const who = p ? `: ${p.drafted_by_name ?? `職員 #${p.drafted_by_id}`}（${inZone(p.updated_at, TIMEZONE).format("M/D HH:mm")}）` : "";
    return (
      <span className="template-name">
        <span className="swatch" style={{ background: s.background }} aria-hidden="true" />
        {name}
        {t.pending_change_id != null && (
          <Tooltip title={`承認待ちの下書きがあります${who}`}>
            <Tag color="gold" className="draft-tag">承認待ち</Tag>
          </Tooltip>
        )}
      </span>
    );
  };

  /** 承認待ちの間は、だれも直接変えられない（バックエンドは 409）。ボタンを無効にして理由を出す */
  const blocked = (t: PlanTemplate, button: React.ReactElement) => {
    const reason = pendingDraftReason(t);
    return reason ? <Tooltip title={reason}><span>{button}</span></Tooltip> : button;
  };

  const tableColumns: ColumnsType<PlanTemplate> = [
    ...columns.map((c) => ("dataIndex" in c && c.dataIndex === "name" ? { ...c, render: nameCell } : c)),
    {
      title: "操作",
      key: "actions",
      width: 260,
      fixed: "right",
      render: (_, t) => {
        const pending = t.pending_change_id != null;
        return (
          <Space size={0} wrap>
            {pending && (
              <Button type="link" size="small" icon={<FileTextOutlined />} onClick={() => viewPending(t)}
                aria-label={`「${t.name}」の下書きを見る`}>
                下書きを見る
              </Button>
            )}
            {writeMode === "direct" && blocked(t,
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor(t)} disabled={!selectedBiz || pending}
                aria-label={`「${t.name}」を編集`}>
                編集
              </Button>,
            )}
            {writeMode === "direct" && blocked(t,
              <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDelete(t)} disabled={pending}
                aria-label={`「${t.name}」を削除`}>
                削除
              </Button>,
            )}
            {writeMode === "draft" && blocked(t,
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor(t)} disabled={!selectedBiz || pending}
                aria-label={`「${t.name}」の変更を下書き`}>
                変更を下書き
              </Button>,
            )}
            {writeMode === "draft" && blocked(t,
              <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDeleteRequest(t)} disabled={pending}
                aria-label={`「${t.name}」の削除を依頼`}>
                削除を依頼
              </Button>,
            )}
          </Space>
        );
      },
    },
  ];

  // 業務を選び直すと ?template=（強調）は外れる。日次ダッシュボードから来たときの「戻る」リンク（state）は残す
  const location = useLocation();
  const selectBiz = (id?: number) => setParams(id ? { biz: String(id) } : {}, { state: location.state });

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>予定テンプレート</h1>
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
          {withoutUpcoming > 0 && (
            <Alert type="warning" showIcon message={`今後の予定が無いテンプレートが ${withoutUpcoming} 件あります。`} description={NO_UPCOMING_HINT} />
          )}
          {selectedBiz && <BizSettingsPanel biz={selectedBiz} canEdit={canEdit} onSaved={bizes.reload} />}
          <ChangeRequestsPanel
            data={changes.data}
            loading={changes.loading}
            error={changes.error}
            onReload={changes.reload}
            onView={openViewer}
          />
          <div className="table-head">
            <Typography.Text type="secondary">
              {selectedBiz ? `${selectedBiz.business_code} ${selectedBiz.name}` : `業務ID ${bizId}`} のテンプレート
              {data ? ` ${data.length} 件` : ""}
              {writeMode !== "direct" && ` ・ ${writeMode === "draft" ? DRAFT_MODE_NOTE : PLAN_SETTINGS_ADMIN_ONLY}`}
            </Typography.Text>
            {/* 作成先は選択中の業務（一覧に無い業務IDの場合は作成・編集できない） */}
            {writeMode === "direct" && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)} disabled={!selectedBiz}>
                新規作成
              </Button>
            )}
            {writeMode === "draft" && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)} disabled={!selectedBiz}>
                新しいテンプレートを下書き
              </Button>
            )}
          </div>
          <Table<PlanTemplate>
            rowKey="id"
            rowClassName={(t) => (t.id === focusTemplateId ? "template-row-focus" : "")}
            columns={tableColumns}
            dataSource={data ?? []}
            loading={loading}
            pagination={false}
            size="middle"
            scroll={{ x: 1180 }}
            locale={{ emptyText: <Empty description="この業務にはテンプレートがありません" /> }}
          />
        </>
      )}
      {selectedBiz && writeMode !== "none" && (
        <TemplateFormModal
          open={editor.open}
          mode={writeMode}
          draft={editor.draft}
          biz={selectedBiz}
          template={editor.template}
          onClose={() => setEditor((s) => ({ ...s, open: false }))}
          onSaved={reloadAll}
        />
      )}
      {selectedBiz && (
        <DraftViewModal
          open={viewer.open}
          change={viewer.change}
          template={templateOf(viewer.change)}
          clientCode={selectedBiz.client_code}
          me={me.data}
          onClose={() => setViewer((v) => ({ ...v, open: false }))}
          onEdit={editDraft}
          onChanged={reloadAll}
        />
      )}
    </div>
  );
}
