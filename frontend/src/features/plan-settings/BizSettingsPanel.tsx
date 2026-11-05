import { EditOutlined } from "@ant-design/icons";
import { Alert, App, Button, Descriptions, Form, Input, InputNumber, Modal, Radio, Space, TimePicker, Typography } from "antd";
import { useState } from "react";
import { bizApi } from "../../domain/biz/api/bizApi";
import {
  clientEventBody, clientEventError, COMPLETION_MODE_LABEL, EXECUTION_TYPE_LABEL, PLAN_SETTINGS_ADMIN_ONLY, RELEASE_MODE_LABEL,
} from "../../domain/biz/lib/settings";
import type { BizMaster, CompletionMode, ExecutionType, ReleaseMode } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";
import { dayjs, type Dayjs } from "../../shared/lib/datetime";

interface Values {
  release_mode: ReleaseMode;
  release_time: Dayjs;
  release_days_before: number;
  execution_type: ExecutionType;
  completion_mode: CompletionMode;
  signal_type?: string | null;
}

interface Props {
  biz: BizMaster;
  /** 編集できるか（業務管理者のみ。できない場合も設定は表示する） */
  canEdit: boolean;
  onSaved: () => void;
}

/**
 * 業務の設定（リリースの方法・顧客側の出来事）。テンプレート設定画面の上に表示し、編集はモーダルで行う。
 * 信号での完了は「顧客側の出来事」（LIGHTWEIGHT）の業務だけ・信号の種類が必須（顧客ごとに一意。バックエンドが判定する）。
 */
export function BizSettingsPanel({ biz, canEdit, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const executionType = Form.useWatch("execution_type", form);
  const completionMode = Form.useWatch("completion_mode", form);

  const openForm = () => {
    form.resetFields();
    form.setFieldsValue({
      release_mode: biz.release_mode,
      release_time: dayjs(biz.release_time.slice(0, 5), "HH:mm"),
      release_days_before: biz.release_days_before,
      execution_type: biz.execution_type,
      completion_mode: biz.completion_mode,
      signal_type: biz.signal_type,
    });
    setError(null);
    setOpen(true);
  };

  const save = async (v: Values) => {
    const problem = clientEventError(v);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await bizApi.update(biz.id, {
        release_mode: v.release_mode,
        release_time: v.release_time.format("HH:mm:00"),
        release_days_before: v.release_days_before,
        ...clientEventBody(v),
      });
      message.success(`${biz.business_code} の設定を保存しました`);
      setOpen(false);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  const release = biz.release_mode === "automatic"
    ? `${RELEASE_MODE_LABEL.automatic}: 予定日の${biz.release_days_before ? `${biz.release_days_before}日前` : "当日"} ${biz.release_time.slice(0, 5)}`
    : RELEASE_MODE_LABEL.manual_approval;

  return (
    <div className="biz-settings">
      <Descriptions
        size="small"
        bordered
        column={1}
        title="業務の設定"
        extra={
          canEdit
            ? <Button size="small" icon={<EditOutlined />} onClick={openForm}>編集</Button>
            : <Typography.Text type="secondary" style={{ fontSize: 12 }}>{PLAN_SETTINGS_ADMIN_ONLY}</Typography.Text>
        }
      >
        <Descriptions.Item label="リリース">{release}</Descriptions.Item>
        <Descriptions.Item label="実行のしかた">{EXECUTION_TYPE_LABEL[biz.execution_type]}</Descriptions.Item>
        {biz.execution_type === "lightweight" && (
          <Descriptions.Item label="完了のしかた">
            {COMPLETION_MODE_LABEL[biz.completion_mode]}
            {biz.signal_type && <> （信号の種類: <span className="mono">{biz.signal_type}</span>）</>}
          </Descriptions.Item>
        )}
      </Descriptions>

      <Modal
        open={open}
        title={`${biz.business_code} ${biz.name} の設定`}
        okText="保存"
        cancelText="キャンセル"
        onOk={() => form.submit()}
        onCancel={() => setOpen(false)}
        confirmLoading={saving}
        destroyOnClose
      >
        {error && <Alert type="error" showIcon message={error} className="modal-alert" />}
        <Form form={form} layout="vertical" onFinish={save} disabled={saving}>
          <Form.Item name="release_mode" label="リリースの方法">
            <Radio.Group>
              <Space direction="vertical">
                {(Object.keys(RELEASE_MODE_LABEL) as ReleaseMode[]).map((m) => <Radio key={m} value={m}>{RELEASE_MODE_LABEL[m]}</Radio>)}
              </Space>
            </Radio.Group>
          </Form.Item>
          <Space size={16} wrap>
            <Form.Item name="release_days_before" label="自動リリース: 予定日の何日前（0 = 当日）">
              <InputNumber min={0} max={30} />
            </Form.Item>
            <Form.Item name="release_time" label="自動リリースの時刻（日本時間）">
              <TimePicker format="HH:mm" minuteStep={5} allowClear={false} />
            </Form.Item>
          </Space>
          <Form.Item name="execution_type" label="実行のしかた">
            <Radio.Group>
              <Space direction="vertical">
                {(Object.keys(EXECUTION_TYPE_LABEL) as ExecutionType[]).map((t) => <Radio key={t} value={t}>{EXECUTION_TYPE_LABEL[t]}</Radio>)}
              </Space>
            </Radio.Group>
          </Form.Item>
          {executionType === "lightweight" && (
            <>
              <Form.Item name="completion_mode" label="完了のしかた">
                <Radio.Group>
                  <Space direction="vertical">
                    {(Object.keys(COMPLETION_MODE_LABEL) as CompletionMode[]).map((m) => <Radio key={m} value={m}>{COMPLETION_MODE_LABEL[m]}</Radio>)}
                  </Space>
                </Radio.Group>
              </Form.Item>
              {completionMode === "automatic" && (
                <Form.Item
                  name="signal_type"
                  label="信号の種類（送信側と合わせる。同じ顧客の業務どうしで重複不可）"
                  rules={[{ required: true, whitespace: true, message: "信号の種類を入力してください" }, { max: 50 }]}
                >
                  <Input className="mono" placeholder="例: data_received" />
                </Form.Item>
              )}
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
