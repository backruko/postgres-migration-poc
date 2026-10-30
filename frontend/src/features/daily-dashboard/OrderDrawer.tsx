import { Alert, App, Button, Descriptions, Drawer, Empty, Form, Input, Modal, Radio, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useState } from "react";
import { orderApi } from "../../domain/ope/api";
import { useOrder } from "../../domain/ope/hooks/useOrder";
import {
  availableActions, CONFLICT_ACTION_LABEL, ORDER_ACTION_LABEL, ORDER_EVENT_LABEL, ORDER_STATUS_COLOR, ORDER_STATUS_LABEL,
  REASON_REQUIRED, SOP_SOURCE_LABEL, startBlockedReason, type OrderAction,
} from "../../domain/ope/lib/orderActions";
import type { ConflictAction, OrderDetail, OrderEvent } from "../../domain/ope/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { inZone, todayIn } from "../../shared/lib/datetime";

interface Props {
  orderId: number | null;
  title: string;
  onClose: () => void;
  /** 操作でオーダーが変わったとき（ダッシュボードを取り直す） */
  onChanged: () => void;
}

const fmt = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D(dd) HH:mm");

/** 記録1件の説明（状態の変更は「前 → 後」、それ以外は種類） */
function describeEvent(e: OrderEvent): string {
  if (e.event_type === "status_changed" && e.from_status && e.to_status) {
    return `${ORDER_STATUS_LABEL[e.from_status]} → ${ORDER_STATUS_LABEL[e.to_status]}`;
  }
  if (e.event_type === "conflict_resolved" && typeof e.detail?.action === "string") {
    return `${ORDER_EVENT_LABEL[e.event_type]}（${CONFLICT_ACTION_LABEL[e.detail.action as ConflictAction] ?? e.detail.action}）`;
  }
  return ORDER_EVENT_LABEL[e.event_type];
}

interface ActionValues {
  text?: string;
  conflictAction?: ConflictAction;
}

/**
 * オーダーの詳細（右から開くドロワー）。状態・SOP バージョン・記録と、操作する職員のロールで使える操作を表示する。
 * 操作の可否の最終判定はバックエンド（ここではボタンの出し分けのみ）。
 */
export function OrderDrawer({ orderId, title, onClose, onChanged }: Props) {
  const { message } = App.useApp();
  const order = useOrder(orderId);
  const me = useCurrentStaff();
  const [form] = Form.useForm<ActionValues>();
  const [pending, setPending] = useState<OrderAction | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const today = todayIn(TIMEZONE);
  const data = order.data;
  const day = data ? inZone(data.scheduled_start, TIMEZONE).format("YYYY-MM-DD") : today;
  const actions = data ? availableActions(data, me.data, today, day) : [];
  const blocked = data ? startBlockedReason(data, today, day) : null;

  const openAction = (action: OrderAction) => {
    form.resetFields();
    form.setFieldsValue({ conflictAction: "accept_plan" });
    setError(null);
    setPending(action);
  };

  const run = async (action: OrderAction, v: ActionValues, current: OrderDetail): Promise<OrderDetail> => {
    const text = v.text?.trim() || undefined;
    switch (action) {
      case "release": return orderApi.release(current.id, text);
      case "approveEarlyStart": return orderApi.approveEarlyStart(current.id, text);
      case "start": return orderApi.start(current.id, text);
      case "hold": return orderApi.hold(current.id, text ?? "");
      case "resume": return orderApi.resume(current.id, text);
      case "finish": return orderApi.finish(current.id, text);
      case "cancel": return orderApi.cancel(current.id, text ?? "");
      case "resolveConflict": return orderApi.resolveConflict(current.id, v.conflictAction ?? "accept_plan", text);
    }
  };

  const submit = async (v: ActionValues) => {
    if (!pending || !data) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await run(pending, v, data);
      message.success(`${ORDER_ACTION_LABEL[pending]}しました（${ORDER_STATUS_LABEL[updated.status]}）`);
      setPending(null);
      order.reload();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "操作できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  const conflictAction = Form.useWatch("conflictAction", form);
  const reasonRequired = pending !== null && (REASON_REQUIRED.has(pending) || (pending === "resolveConflict" && conflictAction === "cancel"));

  return (
    <Drawer open={orderId !== null} onClose={onClose} title={title} width={520} destroyOnClose>
      {order.error && <Alert type="error" showIcon message={`オーダーを取得できませんでした: ${order.error.message}`} />}
      {!data ? (
        order.loading ? <Spin /> : <Empty description="オーダーがありません" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Space size={8} wrap>
            <Tag color={ORDER_STATUS_COLOR[data.status]}>{ORDER_STATUS_LABEL[data.status]}</Tag>
            {data.status_before_hold && <span className="muted">保留前: {ORDER_STATUS_LABEL[data.status_before_hold]}</span>}
            {data.early_start_approved && <Tag color="purple">予定日前の開始を承認済み</Tag>}
          </Space>

          {data.conflict && <Alert type="warning" showIcon message="予定と矛盾しています" description={data.conflict_reason} />}

          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="予定">{fmt(data.scheduled_start)} 〜 {inZone(data.scheduled_end, TIMEZONE).format("HH:mm")}</Descriptions.Item>
            <Descriptions.Item label="SOP バージョン">
              {data.sop_version ? (
                <>
                  <span className="mono">{data.sop_version.version_tag}</span>{" "}
                  <span className="muted">（{SOP_SOURCE_LABEL[data.sop_version.source]}）</span>
                </>
              ) : (
                <span className="muted">{data.released_at ? "なし（リリース時に承認済みのバージョンが無かった）" : "承認済みのバージョンなし"}</span>
              )}
            </Descriptions.Item>
            {data.released_at && <Descriptions.Item label="リリース">{fmt(data.released_at)}</Descriptions.Item>}
            <Descriptions.Item label="オーダー番号"><span className="mono">#{data.id}</span></Descriptions.Item>
          </Descriptions>

          <div>
            {me.data?.status !== "active" ? (
              <Typography.Text type="secondary">操作するには、ナビの「操作する職員」で有効な職員を選んでください。</Typography.Text>
            ) : actions.length ? (
              <Space wrap>
                {actions.map((a) => (
                  <Button key={a} type={a === "cancel" ? "default" : "primary"} danger={a === "cancel"} ghost={a !== "cancel" && a !== actions[0]} onClick={() => openAction(a)}>
                    {ORDER_ACTION_LABEL[a]}
                  </Button>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">この職員のロールで行える操作はありません。</Typography.Text>
            )}
            {blocked && <div className="muted order-blocked">{blocked}</div>}
          </div>

          <div>
            <Typography.Title level={5}>記録</Typography.Title>
            <Timeline
              items={[...data.events].reverse().map((e) => ({
                color: e.event_type === "conflict_raised" ? "orange" : e.source === "system" ? "gray" : "blue",
                children: (
                  <div className="order-event">
                    <div><b>{describeEvent(e)}</b> <span className="muted mono">{fmt(e.occurred_at)}</span></div>
                    <div className="muted">{e.source === "system" ? "システム" : `職員 #${e.actor_staff_id}`}{e.reason ? ` ・ ${e.reason}` : ""}</div>
                  </div>
                ),
              }))}
            />
          </div>
        </Space>
      )}

      <Modal
        open={pending !== null}
        title={pending ? ORDER_ACTION_LABEL[pending] : ""}
        okText={pending ? ORDER_ACTION_LABEL[pending] : ""}
        okButtonProps={{ danger: pending === "cancel" }}
        cancelText="キャンセル"
        onOk={() => form.submit()}
        onCancel={() => setPending(null)}
        confirmLoading={submitting}
        destroyOnClose
      >
        {pending === "cancel" && (
          <Typography.Paragraph type="secondary">オーダーを取り消すと、予定（カレンダー）からも取り消されます。元に戻せません。</Typography.Paragraph>
        )}
        {error && <Alert type="error" showIcon message={error} className="modal-alert" />}
        <Form form={form} layout="vertical" onFinish={submit} disabled={submitting}>
          {pending === "resolveConflict" && (
            <Form.Item name="conflictAction" label="解決方法">
              <Radio.Group onChange={() => form.validateFields(["text"]).catch(() => undefined)}>
                <Space direction="vertical">
                  {(Object.keys(CONFLICT_ACTION_LABEL) as ConflictAction[]).map((a) => (
                    <Radio key={a} value={a}>{CONFLICT_ACTION_LABEL[a]}</Radio>
                  ))}
                </Space>
              </Radio.Group>
            </Form.Item>
          )}
          <Form.Item
            name="text"
            label={reasonRequired ? "理由" : "メモ（任意）"}
            rules={[{ required: reasonRequired, whitespace: true, message: "理由を入力してください" }, { max: 500 }]}
            dependencies={["conflictAction"]}
          >
            <Input.TextArea rows={3} maxLength={500} />
          </Form.Item>
        </Form>
      </Modal>
    </Drawer>
  );
}
