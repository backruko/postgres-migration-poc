import { Alert, App, Checkbox, Form, Input, Modal, Radio, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { sopApi } from "../../domain/biz/api/sopApi";
import {
  CONDITION_HELP, CONDITION_LABEL, conditionError, stepCreateBody, stepFormValues, stepUpdateBody, type ConditionType,
  type StepFormValues,
} from "../../domain/biz/lib/sop";
import type { SopStep } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  versionId: number;
  /** 編集するステップ。null なら追加（最後に付く） */
  step: SopStep | null;
  onClose: () => void;
  onSaved: () => void;
}

const MONTH_OPTIONS = Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: `${i + 1}月` }));
const CONDITION_TYPES: ConditionType[] = ["always", "month_in", "first_in_week"];

/** 草稿のステップの追加と編集（手順・内容・含める条件・承認で次へ） */
export function StepFormModal({ open, versionId, step, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<StepFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conditionType = Form.useWatch("condition_type", form) ?? "always";

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(stepFormValues(step));
      setError(null);
    }
  }, [open, step, form]);

  const onFinish = async (v: StepFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (step === null) {
        const added = await sopApi.addStep(versionId, stepCreateBody(v));
        message.success(`ステップ ${added.step_num}「${added.title}」を追加しました。`);
      } else {
        const body = stepUpdateBody(step, v);
        if (body) {
          await sopApi.updateStep(step.id, body);
          message.success(`ステップ ${step.step_num} を保存しました。`);
        } else {
          message.info("変更はありませんでした。");
        }
      }
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={step ? `ステップ ${step.step_num} の編集` : "ステップを追加"}
      okText={step ? "保存" : "追加"}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      onCancel={onClose}
      width={600}
      forceRender
    >
      <Form<StepFormValues> form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="title" label="手順（見出し）" rules={[{ required: true, whitespace: true, message: "手順を入力してください" }, { max: 100, message: "100 文字までです" }]}>
          <Input maxLength={100} autoComplete="off" />
        </Form.Item>
        <Form.Item name="content" label="内容（任意）" extra="作業者が見る具体的な手順・確認すること。">
          <Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} />
        </Form.Item>
        <Form.Item name="condition_type" label="含める条件" extra={CONDITION_HELP[conditionType as ConditionType]}>
          <Radio.Group>
            <Space direction="vertical">
              {CONDITION_TYPES.map((t) => (
                <Radio key={t} value={t}>{CONDITION_LABEL[t]}</Radio>
              ))}
            </Space>
          </Radio.Group>
        </Form.Item>
        {conditionType === "month_in" && (
          <Form.Item
            name="months"
            label="月"
            rules={[{ validator: () => {
              const problem = conditionError(form.getFieldsValue());
              return problem ? Promise.reject(new Error(problem)) : Promise.resolve();
            } }]}
          >
            <Checkbox.Group options={MONTH_OPTIONS} className="sop-months" />
          </Form.Item>
        )}
        <Form.Item name="is_blocking" valuePropName="checked" extra="オンにすると、このステップが承認されるまで次のステップを開けません（オフなら提出で次が開きます）。">
          <Checkbox>承認で次へ</Checkbox>
        </Form.Item>
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        条件は、オーダーの予定日で判定し、リリースで固定されます。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
    </Modal>
  );
}
