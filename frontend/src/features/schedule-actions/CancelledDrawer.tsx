import { UndoOutlined } from "@ant-design/icons";
import { Alert, App, Button, Drawer, Empty, List, Popconfirm, Spin, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { instanceApi } from "../../domain/plan/api";
import { ORIGIN_LABEL } from "../../domain/plan/lib/describe";
import type { CancelledInstance } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { inZone, monthLabel, timeIn } from "../../shared/lib/datetime";
import { chipStyle } from "../calendar/lib/chipStyle";

const NOT_RESTORABLE: Record<string, string> = {
  past: "時刻が過ぎているため元に戻せません",
};

interface Props {
  open: boolean;
  month: string;
  timezone: string;
  items: CancelledInstance[];
  loading: boolean;
  error: ApiError | null;
  onClose: () => void;
  /** 復元後にカレンダーと一覧を読み直す */
  onRestored: () => void;
}

/**
 * 取り消された予定の一覧（カレンダーには表示されない予定）。
 * 「元に戻す」で取消の直前と同じ日時に復元する。
 */
export function CancelledDrawer({ open, month, timezone, items, loading, error, onClose, onRestored }: Props) {
  const { message } = App.useApp();
  const [restoringId, setRestoringId] = useState<number | null>(null);

  const restore = async (item: CancelledInstance) => {
    setRestoringId(item.instance_id);
    try {
      await instanceApi.restore(item.instance_id);
      message.success(`${item.title} を元に戻しました`);
      onRestored();
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : "元に戻せませんでした。");
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={`取消済みの予定（${monthLabel(month)}）`}
      width={Math.min(460, window.innerWidth)}
    >
      <Typography.Paragraph type="secondary" className="drawer-lead">
        取り消した予定はカレンダーに表示されません。「元に戻す」で、取消の直前と同じ日時に戻せます。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error.message} className="modal-alert" />}
      <Spin spinning={loading && items.length > 0}>
        <List
          loading={loading && items.length === 0}
          dataSource={items}
          locale={{ emptyText: <Empty description="この月に取り消された予定はありません" /> }}
          renderItem={(item) => {
            const start = inZone(item.scheduled_start, timezone);
            const origin = ORIGIN_LABEL[item.origin];
            const blocked = item.not_restorable_reason ? NOT_RESTORABLE[item.not_restorable_reason] ?? "元に戻せません" : null;
            const button = (
              <Button
                size="small"
                icon={<UndoOutlined />}
                disabled={!item.restorable}
                loading={restoringId === item.instance_id}
              >
                元に戻す
              </Button>
            );
            return (
              <List.Item
                actions={[
                  item.restorable ? (
                    <Popconfirm
                      key="restore"
                      title="この予定を元に戻しますか？"
                      description={`${start.format("M/D (dd)")} ${timeIn(item.scheduled_start, timezone)}–${timeIn(item.scheduled_end, timezone)} に戻します。`}
                      okText="元に戻す"
                      cancelText="やめる"
                      onConfirm={() => restore(item)}
                    >
                      {button}
                    </Popconfirm>
                  ) : (
                    <Tooltip key="restore" title={blocked}>
                      {button}
                    </Tooltip>
                  ),
                ]}
              >
                <List.Item.Meta
                  avatar={<span className="item-bar cancelled-bar" style={{ background: chipStyle(item.ui_metadata).background }} />}
                  title={
                    <span className="cancelled-title">
                      {item.title}
                      {origin && <Tag className="cancelled-tag">{origin}</Tag>}
                    </span>
                  }
                  description={
                    <div className="cancelled-meta">
                      <div className="mono">
                        {start.format("YYYY/MM/DD (dd)")} {timeIn(item.scheduled_start, timezone)}–{timeIn(item.scheduled_end, timezone)}
                      </div>
                      <div>
                        <span className="mono">{item.biz_code}</span> {item.biz_name}
                      </div>
                      {item.note && <div>理由: {item.note}</div>}
                      <div className="muted">取消日時: {inZone(item.cancelled_at, timezone).format("YYYY/MM/DD HH:mm")}</div>
                    </div>
                  }
                />
              </List.Item>
            );
          }}
        />
      </Spin>
    </Drawer>
  );
}
