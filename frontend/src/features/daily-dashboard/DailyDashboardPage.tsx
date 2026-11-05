import { LeftOutlined, RightOutlined } from "@ant-design/icons";
import { Alert, Badge, Button, DatePicker, Empty, Space, Spin, Tabs, Tag } from "antd";
import { useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useOpenSignals } from "../../domain/ope/hooks/useOpenSignals";
import { useAttention } from "../../domain/ops/hooks/useAttention";
import { useDailyDashboard } from "../../domain/ops/hooks/useDailyDashboard";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { useAutoRefresh } from "../../shared/hooks/useAutoRefresh";
import { ReturnLink } from "../../shared/ui/ReturnLink";
import { AttentionPanel } from "./AttentionPanel";
import { DailyClientSection } from "./DailyClientSection";
import { OrderDrawer } from "./OrderDrawer";
import { SignalsPanel } from "./SignalsPanel";
import { addDays, calendarDayLabel, dayLabel, parseDateParam, summarize } from "./dailyDashboard";

/** 自動で読み直す間隔（N.4） */
const AUTO_REFRESH_MS = 2 * 60 * 1000;

/** タブ（URL の ?tab=。未指定・不明なら予定） */
const TABS = ["schedule", "attention", "signals"] as const;
type TabKey = (typeof TABS)[number];
const parseTab = (value: string | null): TabKey => (TABS as readonly string[]).includes(value ?? "") ? (value as TabKey) : "schedule";

/**
 * 日次ダッシュボード（全顧客横断）。タブで 予定 / 締切・遅れ / 信号 を切り替える（件数はタブに小さく出す）。
 * - 予定: 選んだ日に開始する全顧客の予定を、顧客 → 業務の順に。その日のシート日付と各営業日カレンダーでの休日も。
 *   過去の日付では、無効化・取消された予定と取り消されたシート日付も理由付きで（実績の確認用）。
 * - 締切・遅れ: 日付によらず、締切が近い・過ぎたオーダー（N.4）。
 * - 信号: 照合できなかった信号（人の対応が必要なもの）。
 * 選択した日とタブは URL の ?date= と ?tab= に保持する（未指定なら今日・予定）。開いている間は 2 分ごとに読み直す。
 */
export function DailyDashboardPage() {
  const [params, setParams] = useSearchParams();
  const today = todayIn(TIMEZONE);
  const date = parseDateParam(params.get("date"), today);
  const tab = parseTab(params.get("tab"));
  const isPast = date < today;
  const dashboard = useDailyDashboard(date, TIMEZONE);
  // 締切が近い・遅れ（日付によらず）と、照合できなかった信号。タブの件数にも使う
  // （計測: /ops/daily ≈ 24 ms・/ops/attention ≈ 11 ms。保存した締切を部分インデックスで引くので軽い）
  const attention = useAttention();
  const me = useCurrentStaff();
  const active = me.data?.status === "active";
  const signals = useOpenSignals(active);
  useAutoRefresh(() => {
    dashboard.reload();
    attention.reload();
    if (active) signals.reload();
  }, AUTO_REFRESH_MS);
  // 開いているオーダー（予定の行・締切の一覧の行をクリックすると右のドロワーで詳細と操作）
  const [openOrder, setOpenOrder] = useState<{ id: number; title: string } | null>(null);
  const openDrawer = (id: number, title: string) => setOpenOrder({ id, title });

  // 日・タブを切り替えても、カレンダーから来たときの「戻る」リンク（location.state）は残す
  const location = useLocation();
  const navigate = (next: { date?: string; tab?: TabKey }) => {
    const nextTab = next.tab ?? tab;
    setParams(
      { date: next.date ?? date, ...(nextTab === "schedule" ? {} : { tab: nextTab }) },
      { replace: true, state: location.state },
    );
  };
  const go = (nextDate: string) => navigate({ date: nextDate });

  const data = dashboard.data?.date === date ? dashboard.data : null; // 別の日のデータを一瞬表示しない
  const summary = data ? summarize(data) : null;
  const attentionItems = attention.data?.items ?? [];
  const lateCount = attentionItems.filter((i) => i.late).length;
  const signalCount = active ? (signals.data?.length ?? 0) : 0;
  const tabLabel = (text: string, count: number, color: string) => (
    <span>
      {text} {count > 0 && <Badge count={count} color={color} size="small" overflowCount={999} />}
    </span>
  );

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">{isPast ? "日々の利用 ・ 実績" : "日々の利用"}</div>
          <h1>
            {dayLabel(date)}
            {dashboard.loading && <Spin size="small" className="header-spin" />}
          </h1>
          <ReturnLink />
        </div>
        {summary && (
          <div className="summary">
            <span>顧客 <b>{summary.clients}</b></span>
            <span>予定 <b>{summary.occurrences}</b> 件</span>
            <span>シートの日付 <b>{summary.milestones}</b> 件</span>
            {isPast && <span>無効化・取消 <b>{summary.voided}</b> 件</span>}
          </div>
        )}
      </header>

      <div className="toolbar-row">
        <Space size={4} wrap aria-label="日付の切り替え">
          <Button icon={<LeftOutlined />} aria-label="前の日" onClick={() => go(addDays(date, -1))} />
          <DatePicker
            value={dayjs(date, "YYYY-MM-DD")}
            allowClear={false}
            format="YYYY/M/D(dd)"
            onChange={(d) => d && go(d.format("YYYY-MM-DD"))}
          />
          <Button icon={<RightOutlined />} aria-label="次の日" onClick={() => go(addDays(date, 1))} />
          <Button onClick={() => go(today)} disabled={date === today}>
            今日
          </Button>
        </Space>
        {data && data.calendars.length > 0 && (
          <Space size={4} wrap aria-label="営業日カレンダーでの扱い">
            {data.calendars.map((c) => (
              <Tag key={c.calendar_id} color={c.is_business_day ? undefined : "red"}>
                {c.name}: {calendarDayLabel(c)}
              </Tag>
            ))}
          </Space>
        )}
      </div>

      {dashboard.error && (
        <Alert
          type="error"
          showIcon
          message={`ダッシュボードを取得できませんでした: ${dashboard.error.message}`}
          action={<Button size="small" onClick={dashboard.reload}>再読み込み</Button>}
        />
      )}

      <Tabs
        activeKey={tab}
        onChange={(key) => navigate({ tab: key as TabKey })}
        items={[
          {
            key: "schedule",
            label: "予定",
            children: data && (data.clients.length === 0 ? (
              <Empty description="この日の予定・シートの日付はありません" />
            ) : (
              <div className="daily-clients">
                {data.clients.map((c) => (
                  <DailyClientSection key={c.client_code} client={c} date={date} timezone={TIMEZONE} onOpenOrder={openDrawer} />
                ))}
              </div>
            )),
          },
          {
            key: "attention",
            // 遅れがあれば赤、まもなくだけなら黄（件数は両方の合計）
            label: tabLabel("締切・遅れ", attentionItems.length, lateCount > 0 ? "red" : "gold"),
            children: <AttentionPanel data={attention.data} today={today} timezone={TIMEZONE} onOpenOrder={openDrawer} />,
          },
          {
            key: "signals",
            label: tabLabel("信号", signalCount, "orange"),
            children: <SignalsPanel signals={signals} active={active} onChanged={dashboard.reload} />,
          },
        ]}
      />

      <OrderDrawer
        orderId={openOrder?.id ?? null}
        title={openOrder?.title ?? ""}
        onClose={() => setOpenOrder(null)}
        onChanged={() => {
          dashboard.reload();
          attention.reload();
        }}
      />
    </div>
  );
}
