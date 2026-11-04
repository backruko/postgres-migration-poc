import { Alert, App, Button, Descriptions, Drawer, Empty, Form, Input, Modal, Radio, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useState } from "react";
import { orderApi } from "../../domain/ope/api";
import { useOrder } from "../../domain/ope/hooks/useOrder";
import {
  availableActions, CONFLICT_ACTION_LABEL, ORDER_ACTION_LABEL, ORDER_EVENT_LABEL, ORDER_STATUS_COLOR, ORDER_STATUS_LABEL,
  REASON_REQUIRED, SOP_SOURCE_LABEL, startBlockedReason, type OrderAction,
} from "../../domain/ope/lib/orderActions";
import {
  availableStepActions, describeStepEvent, rejectionReason, STEP_ACTION_LABEL, STEP_REASON_REQUIRED, STEP_STATE_COLOR,
  STEP_STATE_LABEL, stepBlockedReason, stepProgress, type StepAction,
} from "../../domain/ope/lib/stepActions";
import { KIND_LABEL } from "../../domain/biz/lib/dependencies";
import type { ConflictAction, OrderDependency, OrderDependent, OrderDetail, OrderEvent, OrderStep } from "../../domain/ope/types";
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

/** 確認中の操作（オーダーの操作か、ステップの操作か） */
type PendingAction = { kind: "order"; action: OrderAction } | { kind: "step"; action: StepAction; step: OrderStep };

const pendingLabel = (p: PendingAction) =>
  p.kind === "order" ? ORDER_ACTION_LABEL[p.action] : `ステップ ${p.step.step_num} を${STEP_ACTION_LABEL[p.action]}`;

/**
 * オーダーの詳細（右から開くドロワー）。状態・SOP バージョン・ステップ・記録と、操作する職員のロールで使える操作を表示する。
 * ステップはリリース前はプレビュー（リリース時に確定）。操作の可否の最終判定はバックエンド（ここではボタンの出し分けのみ）。
 */
export function OrderDrawer({ orderId, title, onClose, onChanged }: Props) {
  const { message } = App.useApp();
  const order = useOrder(orderId);
  const me = useCurrentStaff();
  const [form] = Form.useForm<ActionValues>();
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const today = todayIn(TIMEZONE);
  const data = order.data;
  const day = data ? inZone(data.scheduled_start, TIMEZONE).format("YYYY-MM-DD") : today;
  const actions = data ? availableActions(data, me.data, today, day) : [];
  const blocked = data ? startBlockedReason(data, today, day) : null;

  const openAction = (next: PendingAction) => {
    form.resetFields();
    form.setFieldsValue({ conflictAction: "accept_plan" });
    setError(null);
    setPending(next);
  };

  const run = async (p: PendingAction, v: ActionValues, current: OrderDetail): Promise<OrderDetail> => {
    const text = v.text?.trim() || undefined;
    if (p.kind === "step") {
      const stepId = p.step.id as number;
      switch (p.action) {
        case "submit": return orderApi.submitStep(stepId, text);
        case "approve": return orderApi.approveStep(stepId, text);
        case "reject": return orderApi.rejectStep(stepId, text ?? "");
      }
    }
    switch (p.action) {
      case "release": return orderApi.release(current.id, text);
      case "approveEarlyStart": return orderApi.approveEarlyStart(current.id, text);
      case "approveDependencyOverride": return orderApi.approveDependencyOverride(current.id, text ?? "");
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
      message.success(`${pendingLabel(pending)}しました（${ORDER_STATUS_LABEL[updated.status]}）`);
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
  const reasonRequired = pending !== null && (
    pending.kind === "step"
      ? STEP_REASON_REQUIRED.has(pending.action)
      : REASON_REQUIRED.has(pending.action) || (pending.action === "resolveConflict" && conflictAction === "cancel")
  );
  const approvedCount = data ? data.steps.filter((st) => st.state === "approved").length : 0;
  const progress = data && !data.steps_preview ? stepProgress(data.steps.length, approvedCount) : null;

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
                  <Button key={a} type={a === "cancel" ? "default" : "primary"} danger={a === "cancel"} ghost={a !== "cancel" && a !== actions[0]} onClick={() => openAction({ kind: "order", action: a })}>
                    {ORDER_ACTION_LABEL[a]}
                  </Button>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">この職員のロールで行える操作はありません。</Typography.Text>
            )}
            {blocked && <div className="muted order-blocked">{blocked}</div>}
          </div>

          {(data.dependencies.length > 0 || data.dependents.length > 0) && (
            <div>
              <Typography.Title level={5}>依存関係</Typography.Title>
              {data.dependencies.length > 0 && (
                <>
                  <div className="field-label">待つ上流のオーダー</div>
                  <ul className="order-deps">
                    {data.dependencies.map((d) => <UpstreamRow key={d.id} dep={d} />)}
                  </ul>
                </>
              )}
              {data.dependents.length > 0 && (
                <>
                  <div className="field-label">このオーダーを待つ下流のオーダー</div>
                  <ul className="order-deps">
                    {data.dependents.map((d) => <DownstreamRow key={d.id} dep={d} />)}
                  </ul>
                </>
              )}
            </div>
          )}

          <div>
            <Typography.Title level={5}>
              ステップ {progress && <span className="muted">（承認 {progress}）</span>}
            </Typography.Title>
            {data.steps_preview && data.steps.length > 0 && (
              <Typography.Paragraph type="secondary">リリース前のプレビューです（予定日の条件で決まり、リリース時に確定します）。</Typography.Paragraph>
            )}
            {data.steps.length === 0 ? (
              <span className="muted">ステップはありません{data.released_at ? "（作業者が完了にします）" : ""}</span>
            ) : (
              <ol className="order-steps">
                {data.steps.map((st) => (
                  <StepRow
                    key={st.step_template_id}
                    step={st}
                    actions={availableStepActions(data, st, me.data)}
                    blocked={stepBlockedReason(data, st, me.data)}
                    onAction={(a) => openAction({ kind: "step", action: a, step: st })}
                  />
                ))}
              </ol>
            )}
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
        title={pending ? pendingLabel(pending) : ""}
        okText={pending ? (pending.kind === "order" ? ORDER_ACTION_LABEL[pending.action] : STEP_ACTION_LABEL[pending.action]) : ""}
        okButtonProps={{ danger: pending?.kind === "order" && pending.action === "cancel" }}
        cancelText="キャンセル"
        onOk={() => form.submit()}
        onCancel={() => setPending(null)}
        confirmLoading={submitting}
        destroyOnClose
      >
        {pending?.kind === "order" && pending.action === "approveDependencyOverride" && (
          <Typography.Paragraph type="secondary">
            上流のオーダーが完了していないまま、作業者が開始できるようにします。対象は今満たされていないつながりです（後から増えたものには改めて承認が必要です）。
          </Typography.Paragraph>
        )}
        {pending?.kind === "order" && pending.action === "cancel" && (
          <Typography.Paragraph type="secondary">オーダーを取り消すと、予定（カレンダー）からも取り消されます。元に戻せません。</Typography.Paragraph>
        )}
        {error && <Alert type="error" showIcon message={error} className="modal-alert" />}
        <Form form={form} layout="vertical" onFinish={submit} disabled={submitting}>
          {pending?.kind === "order" && pending.action === "resolveConflict" && (
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

/** ステップ1件: 番号・タイトル・状態・内容・最後の人の操作・差し戻しの理由と、使える操作 */
function StepRow({ step, actions, blocked, onAction }: {
  step: OrderStep;
  actions: StepAction[];
  blocked: string | null;
  onAction: (a: StepAction) => void;
}) {
  const humans = step.events.filter((e) => e.source === "human");
  const last = humans[humans.length - 1];
  const rejected = rejectionReason(step);
  return (
    <li className={`order-step ${step.state ?? "preview"}`}>
      <div className="order-step-head">
        <span className="mono order-step-num">{step.step_num}</span>
        <b>{step.title}</b>
        {step.state && <Tag color={STEP_STATE_COLOR[step.state]}>{STEP_STATE_LABEL[step.state]}</Tag>}
        {step.is_blocking && <Tag title="承認されるまで次のステップは開きません">承認で次へ</Tag>}
      </div>
      {step.content && <div className="muted order-step-content">{step.content}</div>}
      {rejected && <Alert type="warning" showIcon message={`差し戻し: ${rejected}`} className="order-step-alert" />}
      {last && !rejected && (
        <div className="muted order-step-last">
          {describeStepEvent(last)}: 職員 #{last.actor_staff_id} ・ {fmt(last.occurred_at)}{last.reason ? ` ・ ${last.reason}` : ""}
        </div>
      )}
      {actions.length > 0 && (
        <Space size={6} className="order-step-actions">
          {actions.map((a) => (
            <Button key={a} size="small" type={a === "reject" ? "default" : "primary"} onClick={() => onAction(a)}>
              {STEP_ACTION_LABEL[a]}
            </Button>
          ))}
        </Space>
      )}
      {blocked && <div className="muted order-blocked">{blocked}</div>}
    </li>
  );
}

/** 待つ上流のオーダー1件: 業務・オーダー番号・状態・満たしているか（「参考」は開始を止めない） */
function UpstreamRow({ dep }: { dep: OrderDependency }) {
  const state = dep.met
    ? <Tag color="green">完了</Tag>
    : dep.overridden
      ? <Tag color="purple">承認済み（待たずに開始可）</Tag>
      : dep.kind === "hard" ? <Tag color="orange">待ち</Tag> : <Tag>未完了</Tag>;
  return (
    <li className="order-dep">
      <span className="mono">{dep.upstream_biz.business_code}</span> {dep.upstream_biz.name}
      {dep.upstream_order_id !== null ? (
        <>
          <span className="mono muted"> #{dep.upstream_order_id}</span>
          {dep.upstream_status && <Tag color={ORDER_STATUS_COLOR[dep.upstream_status]}>{ORDER_STATUS_LABEL[dep.upstream_status]}</Tag>}
          {dep.upstream_scheduled_start && <span className="muted mono">{fmt(dep.upstream_scheduled_start)}</span>}
        </>
      ) : (
        <span className="muted"> 上流のオーダーがありません</span>
      )}
      {state}
      {dep.kind === "informational" && <Tag>{KIND_LABEL.informational}</Tag>}
    </li>
  );
}

/** このオーダーを待つ下流のオーダー1件 */
function DownstreamRow({ dep }: { dep: OrderDependent }) {
  return (
    <li className="order-dep">
      <span className="mono">{dep.downstream_biz.business_code}</span> {dep.downstream_biz.name}
      <span className="mono muted"> #{dep.downstream_order_id}</span>
      <Tag color={ORDER_STATUS_COLOR[dep.downstream_status]}>{ORDER_STATUS_LABEL[dep.downstream_status]}</Tag>
      <span className="muted mono">{fmt(dep.downstream_scheduled_start)}</span>
      {dep.kind === "informational" && <Tag>{KIND_LABEL.informational}</Tag>}
    </li>
  );
}
