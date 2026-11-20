import { Button, Spin, Tag, Typography } from "antd";
import { useNotificationLog } from "../../domain/notifications/hooks/useNotificationLog";
import { TIMEZONE } from "../../shared/config";
import { dayjs } from "../../shared/lib/datetime";
import { deliverySummary, ruleText, statusInfo, suppressedText } from "./alarmLog";

interface Props {
  eventId: number;
  /** 「通知」タブで、この出来事の通知を開く */
  onOpenInLog: (eventId: number) => void;
}

/**
 * 出来事から作った通知。出来事の行を開いたときに読む（通知の記録を event_id で絞る。出来事の側は通知を知らない）。
 * まだ処理していない出来事には、何もない。
 */
export function EventAlarms({ eventId, onOpenInLog }: Props) {
  const log = useNotificationLog({ eventId, limit: 20 });
  if (log.loading && !log.data) return <Spin size="small" />;
  const items = log.data?.items ?? [];
  return (
    <div className="event-alarms">
      <Typography.Text strong>この出来事から作った通知</Typography.Text>
      {items.length === 0 ? (
        <Typography.Paragraph type="secondary" className="event-alarms-empty">
          ありません（当てはまるルールがないか、まだ処理していません）。
        </Typography.Paragraph>
      ) : (
        <ul className="event-alarms-list">
          {items.map((n) => {
            const s = statusInfo(n.status);
            return (
              <li key={n.id}>
                <Tag color={s.color}>{s.label}</Tag>
                <span>{ruleText(n)}</span>
                <span className="muted"> ・ {dayjs(n.created_at).tz(TIMEZONE).format("M/D HH:mm")}</span>
                {n.deliveries.length > 0 && <span className="muted"> ・ {deliverySummary(n)}</span>}
                {n.suppressed_reason && <span className="muted"> ・ {suppressedText(n.suppressed_reason)}</span>}
              </li>
            );
          })}
        </ul>
      )}
      {items.length > 0 && (
        <Button size="small" type="link" onClick={() => onOpenInLog(eventId)} className="event-alarms-open">
          「通知」タブで詳しく見る（送ったアドレス・相手の返事）
        </Button>
      )}
    </div>
  );
}
