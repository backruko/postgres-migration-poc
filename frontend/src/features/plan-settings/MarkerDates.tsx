import { DeleteOutlined, EditOutlined, PlusOutlined, RollbackOutlined } from "@ant-design/icons";
import { App, Button, Space, Switch, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { sheetApi } from "../../domain/plan/api";
import type { ReferenceSheet, ReferenceSheetEvent, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { dayjs } from "../../shared/lib/datetime";
import { EventDateModal } from "./EventDateModal";
import {
  canDelete,
  canMove,
  canRevert,
  EVENT_SOURCE_LABEL,
  eventSource,
  isEditable,
  listedEvents,
  originalDate,
  type EventSource,
} from "./markerForm";

interface Props {
  sheet: ReferenceSheet;
  marker: ReferenceSheetMarker;
  today: string;
  /** 日付を変えたとき（シートの再読み込み用） */
  onChanged: () => void;
}

const fmt = (d: string) => dayjs(d, "YYYY-MM-DD").format("YYYY/M/D(dd)");
const SOURCE_COLOR: Record<EventSource, string | undefined> = {
  rule: "green",
  moved: "orange",
  added: "orange",
  cancelled: "default",
  imported: undefined,
};
const CASCADE_NOTE = "（このマーカーを参照するテンプレートの未来の予定も更新しました）";

/**
 * マーカーの日付の一覧と、日付ごとの変更・削除・ルールに戻す・追加。
 * 今日より前の日付は実績のため読み取り専用（既定では表示せず、「過去も表示」で確認できる）。
 */
export function MarkerDates({ sheet, marker, today, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const [showPast, setShowPast] = useState(false);
  // 追加・変更モーダル（event が null なら追加）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; event: ReferenceSheetEvent | null }>({ open: false, event: null });

  const run = async (action: () => Promise<unknown>, done: string) => {
    try {
      await action();
      message.success(`${done}${CASCADE_NOTE}`);
    } catch (e) {
      message.error(`変更できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
    }
    // 失敗した場合も読み直す（別の画面で変更済みなど、一覧が古い可能性がある）
    onChanged();
  };

  const confirmDelete = (e: ReferenceSheetEvent) =>
    modal.confirm({
      title: `${fmt(e.event_date)} を削除しますか？`,
      content:
        eventSource(e) === "added"
          ? "人が追加した日付のため、記録を残さずに削除します。"
          : `「取消」として記録が残り、${e.rule_date ? "ルールで" : "取り込み直しても"}再び作られることはありません${
              e.revertable || e.rule_date ? "（ルールの日付なら「ルールに戻す」で元に戻せます）" : "（同じ日付を追加し直すと元に戻ります）"
            }。`,
      okText: "削除する",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: () => run(() => sheetApi.deleteEvent(e.id), `「${marker.marker}」の ${fmt(e.event_date)} を削除しました`),
    });

  const confirmRevert = (e: ReferenceSheetEvent) =>
    modal.confirm({
      title: "ルールの日付に戻しますか？",
      content: `${e.is_cancelled ? "取消を解除し、" : `${fmt(e.event_date)} から`}ルールどおりの日付（${fmt(e.rule_date!)} を基準に、休日の扱いを適用）に戻します。`,
      okText: "ルールに戻す",
      cancelText: "キャンセル",
      onOk: () => run(() => sheetApi.revertEvent(e.id), `「${marker.marker}」をルールの日付に戻しました`),
    });

  const columns: ColumnsType<ReferenceSheetEvent> = [
    {
      title: "日付",
      dataIndex: "event_date",
      className: "mono",
      render: (d: string, e) => (e.is_cancelled ? <s className="muted">{fmt(d)}</s> : fmt(d)),
    },
    {
      title: "出どころ",
      key: "source",
      render: (_, e) => {
        const source = eventSource(e);
        const original = originalDate(e);
        return (
          <Space size={4}>
            <Tag color={SOURCE_COLOR[source]}>{EVENT_SOURCE_LABEL[source]}</Tag>
            {(source === "moved" || source === "cancelled") && original && original !== e.event_date && (
              <span className="muted">（元: {fmt(original)}）</span>
            )}
            {source === "cancelled" && (
              <span className="muted">{e.rule_date ? "ルールの日付" : e.original_date ? "取り込んだ日付" : ""}</span>
            )}
          </Space>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_, e) =>
        !isEditable(e, today) ? (
          <span className="muted">実績（変更できません）</span>
        ) : (
          <Space size={0}>
            {canMove(e) && (
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditor({ open: true, event: e })}>
                変更
              </Button>
            )}
            {canRevert(e) && (
              <Button type="link" size="small" icon={<RollbackOutlined />} onClick={() => confirmRevert(e)}>
                ルールに戻す
              </Button>
            )}
            {canDelete(e) && (
              <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDelete(e)}>
                削除
              </Button>
            )}
          </Space>
        ),
    },
  ];

  const rows = listedEvents(marker, today, showPast);

  return (
    <div className="marker-dates">
      <div className="table-head">
        <Space size="middle" wrap>
          <Typography.Text type="secondary">「{marker.marker}」の日付</Typography.Text>
          <label className="inline-switch">
            <Switch size="small" checked={showPast} onChange={setShowPast} /> 過去も表示（実績・変更不可）
          </label>
        </Space>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, event: null })}>
          日付を追加
        </Button>
      </div>
      <Table<ReferenceSheetEvent>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={rows.length > 24 ? { pageSize: 24, size: "small", showSizeChanger: false } : false}
        size="small"
        rowClassName={(e) => (isEditable(e, today) ? "" : "is-past-row")}
        locale={{ emptyText: showPast ? "日付がありません" : "今日以降の日付はありません" }}
      />
      <EventDateModal
        open={editor.open}
        sheet={sheet}
        marker={marker}
        event={editor.event}
        today={today}
        onClose={() => setEditor((s) => ({ ...s, open: false }))}
        onSaved={onChanged}
      />
    </div>
  );
}
