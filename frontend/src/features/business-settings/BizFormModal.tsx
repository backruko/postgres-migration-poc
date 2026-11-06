import { Alert, App, Descriptions, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { bizApi } from "../../domain/biz/api/bizApi";
import { bizBasics, bizUpdateBody, type BizFormValues } from "../../domain/biz/lib/businesses";
import type { BizMaster } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  /** 編集する業務。null なら新規登録 */
  biz: BizMaster | null;
  clientCode: string;
  clientName: string;
  /** 登録で付く業務コード（番号が尽きていれば null） */
  nextCode: string | null;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 業務の登録と編集（業務名・管理者メール・説明）。業務コードは自動で付き、変えられない。
 * リリース・顧客側の出来事の設定は、予定テンプレート設定の「業務の設定」で変える。
 */
export function BizFormModal({ open, biz, clientCode, clientName, nextCode, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<BizFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(biz ? { name: biz.name, manager_email: biz.manager_email ?? "", description: biz.description ?? "" } : {});
      setError(null);
    }
  }, [open, biz, form]);

  const onFinish = async (v: BizFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      if (biz === null) {
        const created = await bizApi.create({ client_code: clientCode, ...bizBasics(v) });
        message.success(`業務 ${created.business_code}「${created.name}」を登録しました。予定テンプレートは「予定テンプレート・業務の設定」から作れます。`, 6);
      } else {
        const body = bizUpdateBody(biz, v);
        if (body) {
          await bizApi.update(biz.id, body);
          message.success(`業務 ${biz.business_code} を保存しました。`);
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
      title={biz ? `業務の編集（${biz.business_code}）` : "業務を登録"}
      okText={biz ? "保存" : "登録"}
      okButtonProps={{ disabled: biz === null && nextCode === null }}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      onCancel={onClose}
      forceRender
    >
      <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="顧客">
          <span className="mono">{clientCode}</span>&nbsp;{clientName}
        </Descriptions.Item>
        <Descriptions.Item label="業務コード">
          {biz ? (
            <span className="mono">{biz.business_code}</span>
          ) : nextCode ? (
            <span><span className="mono">{nextCode}</span> <span className="muted">（自動。登録後は変えられません）</span></span>
          ) : (
            <span className="muted">この顧客の業務番号（9999 まで）を使い切っています</span>
          )}
        </Descriptions.Item>
      </Descriptions>
      <Form<BizFormValues> form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="name" label="業務名" rules={[{ required: true, whitespace: true, message: "業務名を入力してください" }, { max: 100, message: "100 文字までです" }]}>
          <Input maxLength={100} autoComplete="off" />
        </Form.Item>
        <Form.Item name="manager_email" label="管理者のメールアドレス（任意）" rules={[{ type: "email", message: "メールアドレスの形ではありません" }, { max: 100, message: "100 文字までです" }]}>
          <Input maxLength={100} autoComplete="off" />
        </Form.Item>
        <Form.Item name="description" label="説明（任意）">
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  );
}
