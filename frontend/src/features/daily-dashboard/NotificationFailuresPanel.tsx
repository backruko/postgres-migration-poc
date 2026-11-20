import { App, Button, Empty, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { notificationApi } from "../../domain/notifications/api";
import type { FailedNotification, NotificationFailures } from "../../domain/notifications/types";
import { dayjs } from "../../shared/lib/datetime";
import { eventTypeInfo } from "../event-log/eventLog";
import {
  DELIVERY_STATUS_COLOR, NOTIFICATION_STATUS_LABEL, confirmedText, deliveryText, splitByConfirmation,
} from "./notificationFailures";

interface Props {
  data: NotificationFailures | null;
  timezone: string;
  /** 「確認した」を押せるか（作業者・確認者・リリース担当・業務管理者） */
  canConfirm: boolean;
  /** 行をクリックしたとき（オーダーの詳細と操作） */
  onOpenOrder: (orderId: number, title: string) => void;
}

/**
 * 届かなかったアラーム。日次ダッシュボードの「通知の失敗」タブ。
 * 再試行の後も宛先のどれかに届かなかったアラームを、まだ確認していないもの → 確認済み（直近 7 日・薄く）の順に。
 * 宛先ごとの結果と相手のサーバーの返事を出す。「確認した」で帯と件数から消える（だれが・いつを残す）。
 */
export function NotificationFailuresPanel({ data, timezone, canConfirm, onOpenOrder }: Props) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState<number | null>(null);
  const items = data?.items ?? [];
  if (!items.length) return <Empty description="届かなかったアラームはありません" />;
  const { open, confirmed } = splitByConfirmation(items);

  const confirm = async (i: FailedNotification) => {
    setBusy(i.id);
    try {
      await notificationApi.acknowledge(i.id);
      message.success("確認済みにしました");
    } catch (e) {
      message.error(`確認済みにできませんでした: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const columns: ColumnsType<FailedNotification> = [
    {
      title: "日時",
      key: "at",
      width: 100,
      className: "mono",
      render: (_, i) => dayjs(i.created_at).tz(timezone).format("M/D HH:mm"),
    },
    {
      title: "種類",
      key: "type",
      width: 90,
      render: (_, i) => {
        const info = eventTypeInfo(i.event_type);
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "顧客", key: "client", width: 150, render: (_, i) => <span><span className="mono">{i.client_code ?? "—"}</span> {i.client_name}</span> },
    {
      title: "件名",
      key: "subject",
      render: (_, i) => (
        <div>
          <div>{i.subject}</div>
          <Typography.Text type="secondary" className="failure-meta">
            {NOTIFICATION_STATUS_LABEL[i.status] ?? i.status} ・ ルール「{i.rule_name}」
          </Typography.Text>
        </div>
      ),
    },
    {
      title: "宛先と理由",
      key: "deliveries",
      render: (_, i) => (
        <ul className="failure-deliveries">
          {i.deliveries.map((d) => (
            <li key={d.destination_id}>
              <Tag color={DELIVERY_STATUS_COLOR[d.status]}>{deliveryText(d)}</Tag>
              {d.last_error && d.status !== "sent" && <div className="failure-error mono">{d.last_error}</div>}
            </li>
          ))}
        </ul>
      ),
    },
    {
      title: "",
      key: "confirm",
      width: 150,
      render: (_, i) =>
        i.acknowledged_at ? (
          <Typography.Text type="secondary">{confirmedText(i, timezone)}</Typography.Text>
        ) : canConfirm ? (
          <Button
            size="small"
            type="primary"
            loading={busy === i.id}
            onClick={(e) => {
              e.stopPropagation();  // 行のクリック（オーダーを開く）にしない
              void confirm(i);
            }}
          >
            確認した
          </Button>
        ) : (
          <Typography.Text type="secondary">未確認</Typography.Text>
        ),
    },
  ];

  const table = (rows: FailedNotification[], muted: boolean) => (
    <Table<FailedNotification>
      rowKey="id"
      columns={columns}
      dataSource={rows}
      pagination={false}
      size="small"
      scroll={{ x: 900 }}
      rowClassName={muted ? "failure-confirmed-row" : undefined}
      onRow={(i) => (i.order_id ? {
        onClick: () => onOpenOrder(i.order_id as number, i.subject),
        style: { cursor: "pointer" },
      } : {})}
    />
  );

  return (
    <section className="failures-panel" aria-label="届かなかったアラーム">
      <Typography.Text type="secondary">
        再試行の後も宛先に届かなかったアラームです。電話などで対応したら「確認した」を押してください（まだ確認していないものは、
        古くても消えません）。行をクリックするとオーダーを開きます{data?.truncated ? "（ほかにもあります）" : ""}
      </Typography.Text>
      {open.length > 0 ? table(open, false) : <Empty description="まだ確認していないものはありません" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      {confirmed.length > 0 && (
        <>
          <Typography.Title level={5} className="failure-confirmed-title">確認済み（直近 7 日）</Typography.Title>
          {table(confirmed, true)}
        </>
      )}
    </section>
  );
}
