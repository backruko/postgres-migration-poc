import { SettingOutlined } from "@ant-design/icons";
import { Tag } from "antd";
import { Link, useLocation } from "react-router-dom";
import { ORDER_STATUS_COLOR, ORDER_STATUS_LABEL } from "../../domain/ope/lib/orderActions";
import { stepProgress } from "../../domain/ope/lib/stepActions";
import type { DailyClient } from "../../domain/ops/types";
import { timeIn } from "../../shared/lib/datetime";
import { returnState, returnToHere } from "../../shared/lib/returnTo";
import { Icon } from "../calendar/components/Icon";
import { chipStyle } from "../calendar/lib/chipStyle";
import { deadlineLabel, milestoneSource, shortDate, showsOwnDeadline, VOID_REASON_LABEL } from "./dailyDashboard";

interface Props {
  client: DailyClient;
  date: string;
  timezone: string;
  /** 予定の行をクリックしたとき（オーダーのある予定のみ） */
  onOpenOrder: (orderId: number, title: string) => void;
}

/** 1顧客分: その日のシート日付と、業務ごとの予定（オーダーの状態付き。過去の日付では無効化・取消された予定も） */
export function DailyClientSection({ client, date, timezone, onOpenOrder }: Props) {
  const count = client.businesses.reduce((n, b) => n + b.occurrences.length, 0);
  const span = (start: string, end: string) => `${timeIn(start, timezone)}–${timeIn(end, timezone)}`;
  // 予定テンプレートの画面に「この日の日次ダッシュボードに戻る」リンクを出す（ダッシュボード自身の戻り先も引き継ぐ）
  const location = useLocation();
  const backHere = returnState(returnToHere(location, `${shortDate(date)} の日次ダッシュボードに戻る`));

  return (
    <section className="daily-client" aria-label={`${client.client_code} ${client.client_name}`}>
      <header className="daily-client-head">
        <Link to={{ pathname: "/", search: `?client=${client.client_code}&month=${date.slice(0, 7)}` }} className="mono" title="この顧客のカレンダーを開く">
          {client.client_code}
        </Link>
        <span className="daily-client-name">{client.client_name}</span>
        <span className="muted">予定 {count} 件</span>
      </header>

      {client.milestones.length > 0 && (
        <div className="daily-milestones">
          <span className="field-label">シートの日付</span>
          {client.milestones.map((m) => (
            <Tag
              key={m.event_id}
              className={m.is_cancelled ? "daily-milestone cancelled" : "daily-milestone"}
              title={[m.sheet_name, m.marker_description, m.original_date ? `元の日付: ${shortDate(m.original_date)}` : ""].filter(Boolean).join(" ・ ")}
            >
              {m.marker} <span className="muted">{milestoneSource(m)}</span>
            </Tag>
          ))}
        </div>
      )}

      {client.businesses.map((b) => (
        <div key={b.biz_master_id} className="daily-biz">
          <div className="daily-biz-name">
            <span className="mono">{b.biz_code}</span> {b.biz_name}
          </div>
          <ul className="daily-items">
            {b.occurrences.map((o) => {
              const style = chipStyle(o.ui_metadata);
              const order = o.order;
              const open = order ? () => onOpenOrder(order.id, `${b.biz_code} ${o.title}`) : undefined;
              const progress = order ? stepProgress(order.steps_total, order.steps_approved) : null;
              return (
                <li
                  key={o.instance_id}
                  className={order ? "daily-item has-order" : "daily-item"}
                  onClick={open}
                  onKeyDown={(e) => open && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
                  role={order ? "button" : undefined}
                  tabIndex={order ? 0 : undefined}
                  title={order ? "オーダーの詳細と操作" : undefined}
                >
                  <span className="mono daily-time">{span(o.starts_at, o.ends_at)}</span>
                  {order ? (
                    <Tag color={ORDER_STATUS_COLOR[order.status]} className="order-status-tag">{ORDER_STATUS_LABEL[order.status]}</Tag>
                  ) : (
                    <span className="order-status-tag muted">—</span>
                  )}
                  <span className="swatch" style={{ background: style.background, color: style.color }}>
                    <Icon name={style.icon} />
                  </span>
                  <span className={style.bold ? "daily-title bold" : "daily-title"}>{o.title}</span>
                  {o.is_override && <Tag>人が変更</Tag>}
                  {o.origin === "one_off" && <Tag>臨時</Tag>}
                  {o.occurrence_date !== date && <span className="muted">元の日付 {shortDate(o.occurrence_date)}</span>}
                  {progress && <span className="mono muted order-progress" title="承認済みのステップ / ステップ数">{progress}</span>}
                  {order && showsOwnDeadline(order.deadline_at, o.ends_at) && (
                    <span className="muted mono" title="この予定の締切（テンプレートの設定）">締切 {deadlineLabel(order.deadline_at, date, timezone)}</span>
                  )}
                  {order?.late && <Tag color="red" title="締切を過ぎても完了していません">遅れ</Tag>}
                  {order?.due_soon && <Tag color="gold" title="締切が近づいています">まもなく</Tag>}
                  {order?.blocked && <Tag color="orange" title="上流のオーダーが完了するまで開始できません">待ち</Tag>}
                  {order?.conflict && <Tag color="warning" title={order.conflict_reason ?? undefined}>矛盾</Tag>}
                  {/* 行のクリック（オーダーの詳細）とは別に、予定テンプレートの設定画面へ */}
                  <Link
                    to={{ pathname: "/settings/templates", search: `?biz=${b.biz_master_id}&template=${o.template_id}` }}
                    state={backHere}
                    className="daily-template-link"
                    title="予定テンプレートを開く"
                    aria-label={`「${o.title}」の予定テンプレートを開く`}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    <SettingOutlined />
                  </Link>
                </li>
              );
            })}
            {b.voided.map((v) => (
              <li key={v.instance_id} className="daily-item voided">
                <span className="mono daily-time">{span(v.starts_at, v.ends_at)}</span>
                <span className="swatch voided-swatch" />
                <span className="daily-title">{v.title}</span>
                <Tag>{VOID_REASON_LABEL[v.void_reason]}</Tag>
                {v.void_note && <span className="muted">{v.void_note}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
