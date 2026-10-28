import { EditOutlined, PlusOutlined, StopOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useClients } from "../../domain/client/hooks/useClients";
import { sheetApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import { describeRrule, POLICY_LABEL } from "../../domain/plan/lib/describe";
import type { ReferenceSheet, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { MarkerDates } from "./MarkerDates";
import { MarkerFormModal } from "./MarkerFormModal";
import { activeDates, futureManualDates, manualCounts, upcomingDates } from "./markerForm";

const shortDate = (d: string) => dayjs(d, "YYYY-MM-DD").format("M/D(dd)");

/**
 * 年間計画シートの設定（事前設定）画面。
 * 顧客を選択して、その顧客のシートとマーカー（基準日）を一覧する。ルールで日付を作るマーカー（例: 振込日 = 毎月27日）を追加・編集でき、
 * 行を開くと日付ごとに変更・削除・ルールに戻す・追加ができる（今日より前の日付は実績のため読み取り専用）。
 * シートの取り込みは未対応。選択した顧客は URL の ?client= に保持する。
 */
export function SheetSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const clientCode = params.get("client")?.toUpperCase() || null;

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

  // 日付の一覧を開いているマーカー（マーカーの id はシートをまたいで一意）
  const [expanded, setExpanded] = useState<number[]>([]);
  const expand = (markerId: number) => setExpanded((ids) => (ids.includes(markerId) ? ids : [...ids, markerId]));

  const confirmStop = (sheet: ReferenceSheet, m: ReferenceSheetMarker) =>
    modal.confirm({
      title: `「${m.marker}」のルールでの生成をやめますか？`,
      content: `今ある日付 ${activeDates(m).length} 件はそのまま残り、以後はルールで作られなくなります（予定は変わりません）。日付の出どころ（ルール・人の変更・取消）の記録も残ります。`,
      okText: "生成をやめる",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          await sheetApi.updateMarker(sheet.id, m.marker, { rule: null });
          message.success(`「${m.marker}」のルールでの生成をやめました`);
        } catch (e) {
          message.error(`変更できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        sheets.reload();
      },
    });

  const columns = (sheet: ReferenceSheet): ColumnsType<ReferenceSheetMarker> => [
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
    {
      title: "操作",
      key: "actions",
      width: 200,
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
        </Space>
      ),
    },
  ];

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
        <Empty description="この顧客には年間計画シートがありません（シートの取り込みは未対応です）" />
      ) : (
        (sheets.data ?? []).map((sheet) => (
          <section key={sheet.id} className="sheet-block" aria-label={`シート ${sheet.name}`}>
            <div className="table-head">
              <div>
                <Typography.Text strong>{sheet.name}</Typography.Text>{" "}
                <Typography.Text type="secondary" className="mono">
                  {sheet.period_start}〜{sheet.period_end}・マーカー {sheet.markers.length} 件
                </Typography.Text>
              </div>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(sheet, null)}>
                ルールで日付を作るマーカーを追加
              </Button>
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
                expandedRowRender: (m) => <MarkerDates sheet={sheet} marker={m} today={today} onChanged={sheets.reload} />,
              }}
            />
          </section>
        ))
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
