import { Alert, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ORDER_STATUS_COLOR, ORDER_STATUS_LABEL } from "../../domain/ope/lib/orderActions";
import type { Attention, AttentionItem } from "../../domain/ops/types";
import { deadlineLabel } from "./dailyDashboard";

interface Props {
  data: Attention | null;
  today: string;
  timezone: string;
  /** 行をクリックしたとき（オーダーの詳細と操作） */
  onOpenOrder: (orderId: number, title: string) => void;
}

/**
 * 締切が近い・遅れのオーダー（日付によらず。N.4）。日次ダッシュボードの上に、該当があるときだけ出す。
 * 遅れ = 締切を過ぎて完了・取消になっていない、まもなく = テンプレートで設定した「何分前」から締切まで。
 */
export function AttentionPanel({ data, today, timezone, onOpenOrder }: Props) {
  const items = data?.items ?? [];
  if (!items.length) return null;
  const late = items.filter((i) => i.late).length;
  const soon = items.length - late;

  const columns: ColumnsType<AttentionItem> = [
    {
      title: "",
      key: "flag",
      width: 84,
      render: (_, i) => (i.late ? <Tag color="red">遅れ</Tag> : <Tag color="gold">まもなく</Tag>),
    },
    { title: "締切", key: "deadline", width: 110, className: "mono", render: (_, i) => deadlineLabel(i.deadline_at, today, timezone) },
    { title: "顧客", dataIndex: "client_code", width: 72, className: "mono" },
    { title: "業務", key: "biz", render: (_, i) => <span><span className="mono">{i.biz_code}</span> {i.biz_name}</span> },
    { title: "予定", dataIndex: "title" },
    {
      title: "状態",
      dataIndex: "status",
      width: 96,
      render: (s: AttentionItem["status"]) => <Tag color={ORDER_STATUS_COLOR[s]}>{ORDER_STATUS_LABEL[s]}</Tag>,
    },
  ];

  return (
    <section className="attention-panel" aria-label="締切が近い・遅れのオーダー">
      <Alert
        type={late ? "error" : "warning"}
        showIcon
        message={`締切が近い・遅れのオーダー: 遅れ ${late} 件・まもなく ${soon} 件${data?.truncated ? "（ほかにもあります）" : ""}`}
        description="日付によらず、締切の早い順。行をクリックすると詳細と操作を開きます。"
      />
      <Table<AttentionItem>
        rowKey="order_id"
        columns={columns}
        dataSource={items}
        pagination={items.length > 10 ? { pageSize: 10, size: "small", showSizeChanger: false } : false}
        size="small"
        scroll={{ x: 720 }}
        onRow={(i) => ({
          onClick: () => onOpenOrder(i.order_id, `${i.biz_code} ${i.title}`),
          style: { cursor: "pointer" },
        })}
      />
    </section>
  );
}
