import { LeftOutlined, RightOutlined } from "@ant-design/icons";
import { Alert, Button, DatePicker, Empty, Space, Spin, Tag } from "antd";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useDailyDashboard } from "../../domain/ops/hooks/useDailyDashboard";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { DailyClientSection } from "./DailyClientSection";
import { OrderDrawer } from "./OrderDrawer";
import { addDays, calendarDayLabel, dayLabel, parseDateParam, summarize } from "./dailyDashboard";

/**
 * 日次ダッシュボード（全顧客横断・参照のみ）。
 * 選んだ日に開始する全顧客の予定を、顧客 → 業務の順に表示する。その日のシート日付と、各営業日カレンダーでの休日も表示する。
 * 過去の日付では、無効化・取消された予定と取り消されたシート日付も理由付きで表示する（実績の確認用）。
 * 選択した日は URL の ?date= に保持する（未指定なら今日）。
 */
export function DailyDashboardPage() {
  const [params, setParams] = useSearchParams();
  const today = todayIn(TIMEZONE);
  const date = parseDateParam(params.get("date"), today);
  const isPast = date < today;
  const dashboard = useDailyDashboard(date, TIMEZONE);
  // 開いているオーダー（予定の行をクリックすると右のドロワーで詳細と操作）
  const [openOrder, setOpenOrder] = useState<{ id: number; title: string } | null>(null);

  const go = (next: string) => setParams({ date: next }, { replace: true });

  const data = dashboard.data?.date === date ? dashboard.data : null; // 別の日のデータを一瞬表示しない
  const summary = data ? summarize(data) : null;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">{isPast ? "日々の利用 ・ 実績" : "日々の利用"}</div>
          <h1>
            {dayLabel(date)}
            {dashboard.loading && <Spin size="small" className="header-spin" />}
          </h1>
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

      {data &&
        (data.clients.length === 0 ? (
          <Empty description="この日の予定・シートの日付はありません" />
        ) : (
          <div className="daily-clients">
            {data.clients.map((c) => (
              <DailyClientSection
                key={c.client_code}
                client={c}
                date={date}
                timezone={TIMEZONE}
                onOpenOrder={(id, title) => setOpenOrder({ id, title })}
              />
            ))}
          </div>
        ))}
      <OrderDrawer
        orderId={openOrder?.id ?? null}
        title={openOrder?.title ?? ""}
        onClose={() => setOpenOrder(null)}
        onChanged={dashboard.reload}
      />
    </div>
  );
}
