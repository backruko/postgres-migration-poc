import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, DatePicker, Descriptions, Empty, Select, Space, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useSearchParams } from "react-router";
import { useClients } from "../../domain/client/hooks/useClients";
import { useNotificationLog, useNotificationLogEntry } from "../../domain/notifications/hooks/useNotificationLog";
import type { LogEntry, NotificationStatus } from "../../domain/notifications/types";
import { TIMEZONE } from "../../shared/config";
import { dayjs } from "../../shared/lib/datetime";
import {
  KIND_OPTIONS, NOTIFICATION_STATUSES, addressChanged, attemptResultText, deliverySummary, ruleText, statusInfo, suppressedText,
} from "./alarmLog";
import { eventTypeInfo, isDay, periodQuery } from "./eventLog";

const LIMIT = 200;
const at = (iso: string) => dayjs(iso).tz(TIMEZONE).format("YYYY-MM-DD HH:mm");
const atSec = (iso: string) => dayjs(iso).tz(TIMEZONE).format("M/D HH:mm:ss");

/** 通知 1 件の詳しい中身（行を開いたとき）: 宛先ごとの 1 回ごとの試みと、本文 */
function LogEntryDetailView({ entry }: { entry: LogEntry }) {
  const detail = useNotificationLogEntry(entry.id, true);
  return (
    <div className="alarm-detail">
      {entry.suppressed_reason && (
        <Alert type="info" showIcon message={`知らせなかった理由: ${suppressedText(entry.suppressed_reason)}`} className="alarm-suppressed" />
      )}
      {entry.deliveries.map((d) => (
        <section key={d.destination_id} className="alarm-delivery">
          <Typography.Text strong>{d.destination_name}</Typography.Text>{" "}
          <span className="mono muted">（いまのアドレス: {d.address}）</span>
          {addressChanged(d) && <Tag color="gold" className="alarm-address-changed">送った後にアドレスが直されています</Tag>}
          {d.attempt_log.length > 0 ? (
            <table className="alarm-attempts">
              <thead><tr><th>回</th><th>日時</th><th>送ったアドレス</th><th>結果</th><th>相手のサーバーの返事</th></tr></thead>
              <tbody>
                {d.attempt_log.map((a) => (
                  <tr key={a.attempt_no} className={a.ok ? "" : "alarm-attempt-failed"}>
                    <td className="mono">{a.attempt_no}</td>
                    <td className="mono">{atSec(a.attempted_at)}</td>
                    <td className="mono">{a.address}</td>
                    <td>{attemptResultText(a)}</td>
                    <td className="mono">{a.error ?? a.provider_message_id ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Typography.Paragraph type="secondary" className="alarm-no-attempts">
              {d.status === "pending" ? `まだ送っていません（${d.next_attempt_at ? at(d.next_attempt_at) + " に送る予定" : "待ち"}）。` : "1 回ごとの記録はありません（2026-11-12 より前の配信）。"}
            </Typography.Paragraph>
          )}
        </section>
      ))}
      <Descriptions size="small" column={1} bordered className="alarm-body">
        <Descriptions.Item label="出来事">#{entry.event_id}（{eventTypeInfo(entry.event_type).label}）</Descriptions.Item>
        <Descriptions.Item label="本文">
          {detail.loading ? <Spin size="small" /> : <pre className="event-payload">{detail.data?.body ?? ""}</pre>}
        </Descriptions.Item>
      </Descriptions>
    </div>
  );
}

/**
 * 通知の記録（N.5d）。出来事のページの「通知」タブ。どの出来事から・どのルールで・どの宛先へ・何回目にどのアドレスへ送り、
 * どうなったかを新しい順に。絞り込みは URL の ?tab=alarms&status=&kind=&client=&event=&from=&to= に保持する。
 */
export function AlarmLogPanel() {
  const [params, setParams] = useSearchParams();
  const status = NOTIFICATION_STATUSES.some((s) => s.value === params.get("status")) ? (params.get("status") as NotificationStatus) : null;
  const kind = KIND_OPTIONS.some((k) => k.value === params.get("kind")) ? (params.get("kind") as "alarm" | "test") : null;
  const clientCode = params.get("client")?.toUpperCase() || null;
  const eventParam = Number(params.get("event"));
  const eventId = Number.isInteger(eventParam) && eventParam > 0 ? eventParam : null;
  const fromDay = isDay(params.get("from")) ? params.get("from") : null;
  const toDay = isDay(params.get("to")) ? params.get("to") : null;
  const log = useNotificationLog({
    status: status ?? undefined, kind: kind ?? undefined, clientCode: clientCode ?? undefined, eventId: eventId ?? undefined,
    ...periodQuery(fromDay, toDay, TIMEZONE), limit: LIMIT,
  });
  const clients = useClients();

  const select = (next: Record<string, string | null | undefined>) => {
    const merged: Record<string, string | null> = {
      status, kind, client: clientCode, event: eventId ? String(eventId) : null, from: fromDay, to: toDay,
    };
    for (const [k, v] of Object.entries(next)) if (v !== undefined) merged[k] = v;
    const query: Record<string, string> = { tab: "alarms" };
    for (const [k, v] of Object.entries(merged)) if (v) query[k] = v;
    setParams(query, { replace: true });
  };

  const columns: ColumnsType<LogEntry> = [
    { title: "作った日時", key: "at", width: 150, className: "mono", render: (_, e) => at(e.created_at) },
    {
      title: "出来事", key: "event", width: 110,
      render: (_, e) => { const i = eventTypeInfo(e.event_type); return <Tag color={i.color}>{i.label}</Tag>; },
    },
    { title: "顧客", key: "client", width: 150, render: (_, e) => <span><span className="mono">{e.client_code ?? "全顧客"}</span> {e.client_name}</span> },
    { title: "件名", dataIndex: "subject" },
    { title: "ルール", key: "rule", width: 170, render: (_, e) => (e.is_test ? <Tag color="blue">テスト送信</Tag> : ruleText(e)) },
    {
      title: "状態", key: "status", width: 150,
      render: (_, e) => {
        const s = statusInfo(e.status);
        return (
          <span>
            <Tag color={s.color}>{s.label}</Tag>
            {e.acknowledged_at && <div className="muted alarm-ack">確認: {e.acknowledged_by_name}</div>}
          </span>
        );
      },
    },
    { title: "宛先", key: "deliveries", render: (_, e) => (e.deliveries.length ? deliverySummary(e) : <span className="muted">—</span>) },
  ];

  return (
    <section aria-label="通知の記録">
      <div className="toolbar-row">
        <Space size={8} wrap>
          <Select<string> allowClear placeholder="すべての状態" style={{ width: 170 }} value={status ?? undefined}
            options={NOTIFICATION_STATUSES.map((s) => ({ value: s.value, label: s.label }))} onChange={(v?: string) => select({ status: v ?? null })} />
          <Select<string> allowClear placeholder="アラームとテスト送信" style={{ width: 190 }} value={kind ?? undefined}
            options={KIND_OPTIONS} onChange={(v?: string) => select({ kind: v ?? null })} />
          <Select<string> showSearch allowClear placeholder="全顧客" style={{ width: 220 }} value={clientCode ?? undefined}
            options={(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))} optionFilterProp="label"
            loading={clients.loading} onChange={(code?: string) => select({ client: code ?? null })} />
          <DatePicker.RangePicker allowEmpty={[true, true]}
            value={[fromDay ? dayjs(fromDay, "YYYY-MM-DD") : null, toDay ? dayjs(toDay, "YYYY-MM-DD") : null]}
            format="YYYY-MM-DD" placeholder={["開始日", "終了日"]}
            onChange={(range) => select({ from: range?.[0]?.format("YYYY-MM-DD") ?? null, to: range?.[1]?.format("YYYY-MM-DD") ?? null })} />
          {eventId && (
            <Tag closable onClose={() => select({ event: null })} color="blue" className="alarm-event-filter">出来事 #{eventId} の通知</Tag>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} onClick={log.reload}>再読み込み</Button>
      </div>

      <Typography.Paragraph type="secondary">
        作った通知を新しい順に並べます（日本時間）。行を開くと、宛先ごとに何回目にどのアドレスへ送り、相手のサーバーが何と返したかと、
        本文を確かめられます。知らせなかった通知は、その理由が出ます。
      </Typography.Paragraph>
      {log.data?.truncated && (
        <Alert type="info" showIcon className="event-truncated" message={`新しい順に ${LIMIT} 件だけ表示しています。期間・状態・顧客で絞り込んでください。`} />
      )}
      {log.error ? (
        <Alert type="error" showIcon message={`通知の記録を取得できませんでした: ${log.error.message}`} action={<Button size="small" onClick={log.reload}>再読み込み</Button>} />
      ) : (
        <Table<LogEntry>
          rowKey="id" columns={columns} dataSource={log.data?.items ?? []} loading={log.loading} pagination={false} size="middle"
          scroll={{ x: 1100 }} expandable={{ expandedRowRender: (e) => <LogEntryDetailView entry={e} /> }}
          locale={{ emptyText: <Empty description="この条件の通知はありません" /> }}
        />
      )}
    </section>
  );
}
