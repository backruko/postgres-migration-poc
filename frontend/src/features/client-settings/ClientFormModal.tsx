import { Alert, App, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { clientApi } from "../../domain/client/api/clientApi";
import { clientCodeError, normalizeClientCode } from "../../domain/client/lib/clients";
import type { Client } from "../../domain/client/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  /** 名前を変える顧客。null なら新規登録 */
  client: Client | null;
  /** 既にある顧客（無効も含む。コードの重複チェック用） */
  existing: Client[];
  onClose: () => void;
  onSaved: () => void;
}

interface FormValues {
  code?: string;
  name?: string;
}

/** 顧客の登録（コード + 顧客名）と、顧客名の変更。顧客コードは登録後に変えられない */
export function ClientFormModal({ open, client, existing, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(client ? { code: client.code, name: client.name } : {});
      setError(null);
    }
  }, [open, client, form]);

  const onFinish = async (v: FormValues) => {
    const name = (v.name ?? "").trim();
    setSubmitting(true);
    setError(null);
    try {
      if (client === null) {
        const created = await clientApi.create({ code: normalizeClientCode(v.code ?? ""), name });
        message.success(`顧客 ${created.code}「${created.name}」を登録しました。`);
      } else if (name !== client.name) {
        await clientApi.update(client.id, { name });
        message.success(`顧客 ${client.code} の名前を「${name}」にしました。`);
      } else {
        message.info("変更はありませんでした。");
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
      title={client ? `顧客名の変更（${client.code}）` : "顧客を登録"}
      okText={client ? "保存" : "登録"}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      onCancel={onClose}
      forceRender
    >
      <Form<FormValues> form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="code"
          label="顧客コード"
          extra={client ? "顧客コードは変えられません（業務コード・シート・オーダーが使っています）。" : "英数字 4 文字（大文字で保存）。登録後は変えられません。"}
          rules={client ? [] : [{ validator: (_, value: string | undefined) => {
            const problem = clientCodeError(value ?? "", existing);
            return problem ? Promise.reject(new Error(problem)) : Promise.resolve();
          } }]}
        >
          <Input className="mono" maxLength={4} disabled={client !== null} style={{ width: 120, textTransform: "uppercase" }} autoComplete="off" />
        </Form.Item>
        <Form.Item name="name" label="顧客名" rules={[{ required: true, whitespace: true, message: "顧客名を入力してください" }, { max: 100, message: "100 文字までです" }]}>
          <Input maxLength={100} autoComplete="off" />
        </Form.Item>
      </Form>
      {client === null && (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          顧客は削除できません。使わなくなったら一覧の「無効にする」で選択肢から外します。
        </Typography.Paragraph>
      )}
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
    </Modal>
  );
}
