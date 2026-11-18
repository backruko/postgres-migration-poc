import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, DatePicker, Descriptions, Empty, InputNumber, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { useClients } from "../../domain/client/hooks/useClients";
import { useEvents } from "../../domain/events/hooks/useEvents";
import type { DomainEvent } from "../../domain/events/types";
import { TIMEZONE } from "../../shared/config";
import { dayjs } from "../../shared/lib/datetime";
import { AlarmLogPanel } from "./AlarmLogPanel";
import { EventAlarms } from "./EventAlarms";
import {
  EVENT_TYPES, eventTypeInfo, isDay, orderSnapshot, orderStatusLabel, periodQuery, processingOf, recordDelayMinutes,
} from "./eventLog";

/** 一度に出す件数（これより多ければ、期間などで絞るよう案内する） */
const LIMIT = 200;

const at = (iso: string) => dayjs(iso).tz(TIMEZONE).format("YYYY-MM-DD HH:mm");

/** タブ（URL の ?tab=。未指定なら出来事） */
const TABS = ["events", "alarms"] as const;
type TabKey = (typeof TABS)[number];

/**
 * 記録された出来事と通知（N.2c / N.5d。読み取りのみ）。タブ: 出来事 / 通知（通知の記録）。
 * 出来事 → 通知 → 配信 → 1 回ごとの試み を、このページでたどれる（QA・運用の確認。2026-11-12 の決定）。
 */
export function EventLogPage() {
  const [params, setParams] = useSearchParams();
  const tab: TabKey = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as TabKey) : "events";
  // 出来事の行から「通知」タブへ: その出来事の通知だけを出す
  const openInLog = (eventId: number) => setParams({ tab: "alarms", event: String(eventId) });
  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">確認</div>
          <h1>記録された出来事と通知</h1>
        </div>
      </header>
      <Tabs
        activeKey={tab}
        onChange={(key) => setParams(key === "events" ? {} : { tab: key }, { replace: true })}
        items={[
          { key: "events", label: "出来事", children: tab === "events" && <EventsPanel onOpenInLog={openInLog} /> },
          { key: "alarms", label: "通知", children: tab === "alarms" && <AlarmLogPanel /> },
        ]}
      />
    </div>
  );
}

/**
 * 出来事のタブ（N.2c）。締切の監視が記録した「遅れ」「まもなく」と、宛先のテスト送信を、起きた日時の新しい順に並べる。
 * 絞り込みは URL の ?type=&client=&order=&from=&to= に保持する（from / to は日本時間の日付。両端を含む）。
 * 行を開くと、記録した時点の中身と、その出来事から作った通知が出る。
 */
function EventsPanel({ onOpenInLog }: { onOpenInLog: (eventId: number) => void }) {
  const [params, setParams] = useSearchParams();
  const type = EVENT_TYPES.some((t) => t.value === params.get("type")) ? (params.get("type") as string) : null;
  const clientCode = params.get("client")?.toUpperCase() || null;
  const orderParam = Number(params.get("order"));
  const orderId = Number.isInteger(orderParam) && orderParam > 0 ? orderParam : null;
  const fromDay = isDay(params.get("from")) ? params.get("from") : null;
  const toDay = isDay(params.get("to")) ? params.get("to") : null;

  const period = periodQuery(fromDay, toDay, TIMEZONE);
  const events = useEvents({
    eventType: type ?? undefined, clientCode: clientCode ?? undefined, orderId: orderId ?? undefined, ...period, limit: LIMIT,
  });
  const clients = useClients();
  const rows = useMemo(() => events.data?.items ?? [], [events.data]);

  const select = (next: { type?: string | null; client?: string | null; order?: number | null; from?: string | null; to?: string | null }) => {
    const merged = {
      type: next.type === undefined ? type : next.type,
      client: next.client === undefined ? clientCode : next.client,
      order: next.order === undefined ? orderId : next.order,
      from: next.from === undefined ? fromDay : next.from,
      to: next.to === undefined ? toDay : next.to,
    };
    const query: Record<string, string> = {};
    for (const [k, v] of Object.entries(merged)) if (v !== null && v !== "") query[k] = String(v);
    setParams(query, { replace: true });
  };

  const columns: ColumnsType<DomainEvent> = [
    { title: "起きた日時", key: "occurred", width: 150, className: "mono", render: (_, e) => at(e.occurred_at) },
    {
      title: "種類",
      key: "type",
      width: 100,
      render: (_, e) => {
        const info = eventTypeInfo(e.event_type);
        return <Tag color={info.color} title={e.event_type}>{info.label}</Tag>;
      },
    },
    {
      title: "顧客",
      key: "client",
      render: (_, e) => <span><span className="mono">{e.client_code ?? "—"}</span> {orderSnapshot(e.payload).clientName}</span>,
    },
    {
      title: "業務 / 予定",
      key: "biz",
      render: (_, e) => {
        if (e.subject_type !== "order") return <span className="muted">宛先「{String(e.payload.destination_name ?? "")}」</span>;
        const s = orderSnapshot(e.payload);
        return <span>{s.bizName}<span className="muted"> / {s.templateName}</span></span>;
      },
    },
    { title: "予定日", key: "day", width: 110, className: "mono", render: (_, e) => orderSnapshot(e.payload).occurrenceDate },
    {
      title: "オーダー",
      key: "order",
      width: 140,
      render: (_, e) => e.subject_type !== "order" ? <span className="muted">—</span> : (
        <span>
          <span className="mono">#{e.subject_id}</span>{" "}
          <span className="muted">{orderStatusLabel(orderSnapshot(e.payload).status)}</span>
        </span>
      ),
    },
    {
      title: "記録",
      key: "recorded",
      width: 150,
      render: (_, e) => {
        const delay = recordDelayMinutes(e);
        return (
          <span className="mono" title={`記録した日時 ${at(e.recorded_at)}`}>
            {at(e.recorded_at).slice(11)}
            {delay > 1 && <span className="event-delay"> {delay} 分後</span>}
          </span>
        );
      },
    },
    {
      title: "通知の処理",
      key: "processing",
      width: 140,
      render: (_, e) => {
        const p = processingOf(e, TIMEZONE);
        return <Tag color={p.kind === "done" ? "green" : p.kind === "failing" ? "red" : undefined}>{p.label}</Tag>;
      },
    },
  ];

  const expanded = (e: DomainEvent) => (
    <Descriptions size="small" column={1} bordered className="event-detail">
      <Descriptions.Item label="種類（名前）"><span className="mono">{e.event_type}</span>（形の版 {e.schema_version}）</Descriptions.Item>
      <Descriptions.Item label="締切">{orderSnapshot(e.payload).deadlineAt ? at(orderSnapshot(e.payload).deadlineAt) : "—"}</Descriptions.Item>
      <Descriptions.Item label="重複を防ぐキー"><span className="mono">{e.dedupe_key}</span></Descriptions.Item>
      {e.last_error && <Descriptions.Item label="最後の失敗">{e.last_error}</Descriptions.Item>}
      <Descriptions.Item label="中身（起きた時点の写し）">
        <pre className="event-payload">{JSON.stringify(e.payload, null, 2)}</pre>
      </Descriptions.Item>
      <Descriptions.Item label="通知">
        <EventAlarms eventId={e.id} onOpenInLog={onOpenInLog} />
      </Descriptions.Item>
    </Descriptions>
  );

  return (
    <section aria-label="記録された出来事">
      <div className="toolbar-row">
        <Space size={8} wrap>
          <Select<string>
            allowClear
            placeholder="すべての種類"
            style={{ width: 150 }}
            value={type ?? undefined}
            options={EVENT_TYPES.map((t) => ({ value: t.value, label: t.label }))}
            onChange={(v?: string) => select({ type: v ?? null })}
          />
          <Select<string>
            showSearch
            allowClear
            placeholder="全顧客"
            style={{ width: 240 }}
            value={clientCode ?? undefined}
            options={(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))}
            optionFilterProp="label"
            loading={clients.loading}
            onChange={(code?: string) => select({ client: code ?? null })}
          />
          <InputNumber<number>
            placeholder="オーダー ID"
            min={1}
            precision={0}
            style={{ width: 130 }}
            value={orderId}
            onChange={(v) => select({ order: v ?? null })}
          />
          <DatePicker.RangePicker
            allowEmpty={[true, true]}
            value={[fromDay ? dayjs(fromDay, "YYYY-MM-DD") : null, toDay ? dayjs(toDay, "YYYY-MM-DD") : null]}
            format="YYYY-MM-DD"
            placeholder={["開始日", "終了日"]}
            onChange={(range) =>
              select({ from: range?.[0]?.format("YYYY-MM-DD") ?? null, to: range?.[1]?.format("YYYY-MM-DD") ?? null })}
          />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={events.reload}>再読み込み</Button>
      </div>

      <Typography.Paragraph type="secondary">
        締切の監視（毎分）が記録した「遅れ」（締切を過ぎても完了していない）と「まもなく」（知らせの時刻を過ぎた）、
        宛先のテスト送信を、起きた日時（日本時間）の新しい順に並べます。読み取りのみです。
        1 件のオーダーにつき締切ごとに 1 回だけ記録され、締切が動けばもう一度記録されます。
        行を開くと、記録した時点の中身と、その出来事から作った通知を確かめられます。
      </Typography.Paragraph>

      {events.data?.truncated && (
        <Alert type="info" showIcon className="event-truncated"
          message={`新しい順に ${LIMIT} 件だけ表示しています。古いものは期間・顧客・種類で絞り込んでください。`} />
      )}
      {events.error ? (
        <Alert type="error" showIcon message={`出来事を取得できませんでした: ${events.error.message}`}
          action={<Button size="small" onClick={events.reload}>再読み込み</Button>} />
      ) : (
        <Table<DomainEvent>
          rowKey="id"
          columns={columns}
          dataSource={rows}
          loading={events.loading}
          pagination={false}
          size="middle"
          scroll={{ x: 1000 }}
          expandable={{ expandedRowRender: expanded }}
          locale={{ emptyText: <Empty description="この条件で記録された出来事はありません" /> }}
        />
      )}
    </section>
  );
}
