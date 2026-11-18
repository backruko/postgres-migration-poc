import { DeleteOutlined, EditOutlined, FileAddOutlined, PlusOutlined, StopOutlined, UploadOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Radio, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useSearchParams } from "react-router";
import {
  canEditPlanSettings,
  canPreviewPlanSettings,
  PLAN_SETTINGS_ADMIN_ONLY,
  PLAN_SETTINGS_PREVIEW_ONLY,
} from "../../domain/biz/lib/settings";
import { useClients } from "../../domain/client/hooks/useClients";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { sheetApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import { describeRrule, POLICY_LABEL } from "../../domain/plan/lib/describe";
import { asMarkerInUseDetails, markerInUseText } from "../../domain/plan/lib/sheets";
import type { ReferenceSheet, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { MarkerDates } from "./MarkerDates";
import { MarkerFormModal } from "./MarkerFormModal";
import { SheetFormModal } from "./SheetFormModal";
import { SheetImportModal } from "./SheetImportModal";
import { activeDates, futureManualDates, manualCounts, upcomingDates } from "./markerForm";

const shortDate = (d: string) => dayjs(d, "YYYY-MM-DD").format("M/D(dd)");

/**
 * 年間計画シートの設定（事前設定）画面。
 * 顧客を選択して、その顧客のシートとマーカー（基準日）を一覧する。ルールで日付を作るマーカー（例: 振込日 = 毎月27日）を追加・編集でき、
 * 行を開くと日付ごとに変更・削除・ルールに戻す・追加ができる（今日より前の日付は実績のため読み取り専用）。
 * 業務管理者は、シートを JSON ファイルから取り込める（新しいシートを作る・今あるシートを更新する）。選択した顧客は URL の ?client= に保持する。
 * 業務管理者は、画面でも空のシートを作り（名前と期間）、名前と期間を変え、マーカーを「ルールで作る」か「日付を選んで作る」で足し、
 * マーカーの名前を変え（テンプレートも新しい名前になる）、使われていないマーカーを消せる（E.3、2026-11-11）。
 */
export function SheetSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const clientCode = params.get("client")?.toUpperCase() || null;
  // マーカー・日付の変更は業務管理者だけ（見るのは誰でも）
  const me = useCurrentStaff();
  const canEdit = canEditPlanSettings(me.data);
  // リリース担当は、取り込みの画面でファイルを選んで「取り込むと何が変わるか」を見るまで（取り込みはできない）
  const canPreview = canPreviewPlanSettings(me.data);
  const adminOnly = canPreview ? PLAN_SETTINGS_PREVIEW_ONLY : PLAN_SETTINGS_ADMIN_ONLY;

  const clients = useClients();
  const calendars = useBusinessCalendars();
  const sheets = useReferenceSheets(clientCode, clientCode !== null);
  const today = todayIn(TIMEZONE);
  const calendarName = (id: number | null) => calendars.data?.find((c) => c.id === id)?.code ?? (id === null ? null : `ID ${id}`);

  // 作成・編集モーダル（marker が null なら新規作成）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; sheet: ReferenceSheet | null; marker: ReferenceSheetMarker | null }>({
    open: false,
    sheet: null,
    marker: null,
  });
  const openEditor = (sheet: ReferenceSheet, marker: ReferenceSheetMarker | null) => setEditor({ open: true, sheet, marker });

  const [importOpen, setImportOpen] = useState(false);
  // シートの作成・編集（sheet が null なら新しいシート）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [sheetEditor, setSheetEditor] = useState<{ open: boolean; sheet: ReferenceSheet | null }>({ open: false, sheet: null });

  // 日付の一覧を開いているマーカー（マーカーの id はシートをまたいで一意）
  const [expanded, setExpanded] = useState<number[]>([]);
  const expand = (markerId: number) => setExpanded((ids) => (ids.includes(markerId) ? ids : [...ids, markerId]));

  // 生成をやめる: 日付を残す（以後は手で管理する）か、今日以降の日付も取り消す（業務が終わった）かを選ぶ。既定は残す
  const confirmStop = (sheet: ReferenceSheet, m: ReferenceSheetMarker) => {
    const upcoming = activeDates(m).filter((d) => d >= today).length;
    let cancelFuture = false;
    modal.confirm({
      title: `「${m.marker}」のルールでの生成をやめますか？`,
      width: 520,
      content: (
        <Space direction="vertical" size={8}>
          <span>以後はルールで日付が作られなくなります。過去の日付（実績）と、日付の出どころの記録はそのまま残ります。</span>
          <Radio.Group defaultValue={false} onChange={(e) => (cancelFuture = e.target.value as boolean)}>
            <Space direction="vertical" size={4}>
              <Radio value={false}>
                日付を残す（以後は手で管理する）<br />
                <span className="muted">今日以降の {upcoming} 件と、参照しているテンプレートの予定はそのまま</span>
              </Radio>
              <Radio value={true}>
                今日以降の日付も取り消す（この業務は終わり）<br />
                <span className="muted">
                  今日以降の {upcoming} 件を「取消」として記録し、参照しているテンプレートの今日以降の予定も無くなります
                </span>
              </Radio>
            </Space>
          </Radio.Group>
        </Space>
      ),
      okText: "生成をやめる",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sheetApi.updateMarker(sheet.id, m.marker, { rule: null, cancel_future_dates: cancelFuture });
          message.success(
            `「${m.marker}」のルールでの生成をやめました${cancelFuture ? `（今日以降の ${upcoming} 件を取り消しました）` : ""}`,
          );
        } catch (e) {
          message.error(`変更できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        sheets.reload();
      },
    });
  };

  // マーカーの削除: テンプレート（削除済みも）か承認待ちの下書きが参照していればサーバーが断る（理由にテンプレートを挙げる）
  const confirmDelete = (sheet: ReferenceSheet, m: ReferenceSheetMarker) =>
    modal.confirm({
      title: `マーカー「${m.marker}」を削除しますか？`,
      content: `「${m.marker}」と、その ${activeDates(m).length} 日の日付を削除します。元に戻せません。テンプレート（削除したものも）や承認待ちの下書きが参照しているマーカーは削除できません。`,
      okText: "削除する",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sheetApi.deleteMarker(sheet.id, m.marker);
          message.success(`マーカー「${m.marker}」を削除しました`);
        } catch (e) {
          // 使われているマーカー: サーバーの理由（details）から日本語の文を作る。形が違えば英語のメッセージのまま
          const inUse = e instanceof ApiError ? asMarkerInUseDetails(e.details) : null;
          const reason = inUse ? markerInUseText(inUse) : e instanceof ApiError ? e.message : "通信に失敗しました。";
          message.error(`削除できませんでした: ${reason}`, 10);
        }
        sheets.reload();
      },
    });

  const columns = (sheet: ReferenceSheet): ColumnsType<ReferenceSheetMarker> =>
    canEdit ? [...baseColumns, actionColumn(sheet)] : baseColumns;

  const baseColumns: ColumnsType<ReferenceSheetMarker> = [
    { title: "マーカー", dataIndex: "marker", render: (v: string) => <b>{v}</b> },
    { title: "説明", dataIndex: "description", render: (v: string | null) => v ?? <span className="muted">—</span> },
    {
      title: "日付の作り方",
      key: "rule",
      render: (_, m) =>
        m.is_generated ? (
          <Space size={4} wrap>
            <Tag color="green">自動生成</Tag>
            <span>{describeRrule(m.rrule ?? "")}</span>
            <span className="muted">
              ・{POLICY_LABEL[m.holiday_policy]}
              {calendarName(m.business_calendar_id) && `（${calendarName(m.business_calendar_id)}）`}
            </span>
          </Space>
        ) : (
          <Tag>取り込み</Tag>
        ),
    },
    { title: "日数", key: "count", width: 64, className: "mono", render: (_, m) => activeDates(m).length },
    {
      title: "直近の日付",
      key: "upcoming",
      className: "mono",
      render: (_, m) => upcomingDates(m, today, 3).map(shortDate).join("、") || <span className="muted">なし</span>,
    },
    {
      title: "人の変更",
      key: "manual",
      render: (_, m) => {
        const { edited, cancelled } = manualCounts(m);
        if (!m.is_generated || (!edited && !cancelled)) return <span className="muted">—</span>;
        // 人が編集した未来の日付はルールを変えても残るため、ルールの日付と重なっていないか確認できるよう日付を出す
        const future = futureManualDates(m, today);
        return (
          <Space direction="vertical" size={2}>
            <span title="ルールを変えても維持されます">
              {edited > 0 && `編集 ${edited}`}
              {edited > 0 && cancelled > 0 && "・"}
              {cancelled > 0 && `取消 ${cancelled}`}
            </span>
            {future.length > 0 && (
              <Tag
                color="orange"
                className="clickable-tag"
                role="button"
                tabIndex={0}
                title="ルールを変えても残る、人が編集した未来の日付（クリックで日付の一覧を開く）"
                onClick={() => expand(m.id)}
                onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && (ev.preventDefault(), expand(m.id))}
              >
                今後: {future.map(shortDate).join("、")}
              </Tag>
            )}
          </Space>
        );
      },
    },
  ];

  const actionColumn = (sheet: ReferenceSheet): ColumnsType<ReferenceSheetMarker>[number] => ({
    title: "操作",
    key: "actions",
    width: 280,
    fixed: "right",
    render: (_, m) => (
      <Space size={0}>
        <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor(sheet, m)} aria-label={`「${m.marker}」を編集`}>
          編集
        </Button>
        {m.is_generated && (
          <Button
            type="link"
            size="small"
            danger
            icon={<StopOutlined />}
            onClick={() => confirmStop(sheet, m)}
            aria-label={`「${m.marker}」のルールでの生成をやめる`}
          >
            生成をやめる
          </Button>
        )}
        <Button
          type="link"
          size="small"
          danger
          icon={<DeleteOutlined />}
          onClick={() => confirmDelete(sheet, m)}
          aria-label={`「${m.marker}」を削除`}
        >
          削除
        </Button>
      </Space>
    ),
  });

  const clientOptions = (clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code}　${c.name}` }));
  // URL の顧客が一覧に無い場合も、選択中であることが分かるようにする
  const unknownOption = clientCode && clients.data && !clients.data.some((c) => c.code === clientCode) ? [{ value: clientCode, label: clientCode }] : [];

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>年間計画シート</h1>
        </div>
        <Space wrap>
          {canEdit && clientCode !== null && (
            <Button
              type="primary"
              icon={<FileAddOutlined />}
              onClick={() => setSheetEditor({ open: true, sheet: null })}
              disabled={sheets.data === null || sheets.data === undefined}
            >
              新しいシート
            </Button>
          )}
          {(canEdit || canPreview) && clientCode !== null && (
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)} disabled={sheets.data === null || sheets.data === undefined}>
              {canEdit ? "シートを取り込む" : "取り込みの内容を確認"}
            </Button>
          )}
        </Space>
      </header>

      <label className="field">
        <span className="field-label">顧客</span>
        <Select<string>
          id="client"
          showSearch
          allowClear
          placeholder="顧客を選択（顧客コード・顧客名で検索）"
          value={clientCode ?? undefined}
          options={[...unknownOption, ...clientOptions]}
          optionFilterProp="label"
          loading={clients.loading}
          onChange={(code?: string) => setParams(code ? { client: code } : {})}
          notFoundContent={clients.loading ? "読み込み中…" : "該当する顧客がありません"}
          style={{ width: "min(420px, 100%)" }}
        />
      </label>

      {clients.error && <Alert type="warning" showIcon message={`顧客の一覧を取得できませんでした: ${clients.error.message}`} />}
      {sheets.error && (
        <Alert type="error" showIcon message={sheets.error.message} action={<Button size="small" onClick={sheets.reload}>再読み込み</Button>} />
      )}

      {clientCode === null ? (
        <Empty description="顧客を選択してください" />
      ) : sheets.data?.length === 0 ? (
        <Empty
          description={
            canEdit
              ? "この顧客には年間計画シートがありません。「新しいシート」で作るか、「シートを取り込む」から JSON ファイルを取り込んでください"
              : `この顧客には年間計画シートがありません（${adminOnly}）`
          }
        />
      ) : (
        (sheets.data ?? []).map((sheet) => (
          <section key={sheet.id} className="sheet-block" aria-label={`シート ${sheet.name}`}>
            <div className="table-head">
              <div>
                <Typography.Text strong>{sheet.name}</Typography.Text>{" "}
                <Typography.Text type="secondary" className="mono">
                  {sheet.period_start}〜{sheet.period_end}・マーカー {sheet.markers.length} 件
                  {!canEdit && `・${adminOnly}`}
                </Typography.Text>
              </div>
              {canEdit && (
                <Space wrap>
                  <Button icon={<EditOutlined />} onClick={() => setSheetEditor({ open: true, sheet })}>
                    シートを編集
                  </Button>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(sheet, null)}>
                    マーカーを追加
                  </Button>
                </Space>
              )}
            </div>
            <Table<ReferenceSheetMarker>
              rowKey="id"
              columns={columns(sheet)}
              dataSource={sheet.markers}
              loading={sheets.loading}
              pagination={false}
              size="middle"
              scroll={{ x: 980 }}
              expandable={{
                expandedRowKeys: expanded,
                onExpandedRowsChange: (keys) => setExpanded(keys as number[]),
                expandedRowRender: (m) => <MarkerDates sheet={sheet} marker={m} today={today} canEdit={canEdit} onChanged={sheets.reload} />,
              }}
            />
          </section>
        ))
      )}

      {clientCode !== null && (
        <SheetImportModal
          open={importOpen}
          canImport={canEdit}
          clientCode={clientCode}
          sheets={sheets.data ?? []}
          onClose={() => setImportOpen(false)}
          onImported={sheets.reload}
        />
      )}

      {canEdit && clientCode !== null && (
        <SheetFormModal
          open={sheetEditor.open}
          clientCode={clientCode}
          sheet={sheetEditor.sheet}
          sheets={sheets.data ?? []}
          onClose={() => setSheetEditor((x) => ({ ...x, open: false }))}
          onSaved={sheets.reload}
        />
      )}

      {editor.sheet && (
        <MarkerFormModal
          open={editor.open}
          sheet={editor.sheet}
          marker={editor.marker}
          onClose={() => setEditor((s) => ({ ...s, open: false }))}
          onSaved={sheets.reload}
        />
      )}
    </div>
  );
}
