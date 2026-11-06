import { Alert, App, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { sopApi } from "../../domain/biz/api/sopApi";
import { VERSION_TAG_PATTERN } from "../../domain/biz/lib/sop";
import type { SopVersion } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  draft: SopVersion | null;
  /** 業務の版（タグの重複チェック用） */
  versions: SopVersion[];
  onClose: () => void;
  onSaved: () => void;
}

/** 草稿のタグの変更 */
export function RenameDraftModal({ open, draft, versions, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ version_tag?: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({ version_tag: draft?.version_tag });
      setError(null);
    }
  }, [open, draft, form]);

  const onFinish = async (v: { version_tag?: string }) => {
    if (!draft) return;
    const tag = (v.version_tag ?? "").trim();
    if (tag === draft.version_tag) {
      onClose();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await sopApi.renameDraft(draft.id, tag);
      message.success(`タグを ${tag} にしました。`);
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} title="草稿のタグを変更" okText="保存" cancelText="キャンセル" confirmLoading={submitting} onOk={() => form.submit()} onCancel={onClose} forceRender>
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="version_tag"
          label="タグ"
          rules={[
            { required: true, whitespace: true, message: "タグを入力してください" },
            { pattern: VERSION_TAG_PATTERN, message: "v1.2.0 や v1.2.0-teamA の形で入力してください" },
            { validator: (_, value: string | undefined) =>
                versions.some((x) => x.id !== draft?.id && x.version_tag === (value ?? "").trim())
                  ? Promise.reject(new Error("このタグは既にあります"))
                  : Promise.resolve() },
          ]}
        >
          <Input className="mono" maxLength={50} autoComplete="off" style={{ width: 240 }} />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  );
}
