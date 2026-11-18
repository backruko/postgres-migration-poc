import { Alert, App, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";
import { sopApi } from "../../domain/biz/api/sopApi";
import { newDraftSource, SOP_STATE_LABEL, suggestNextTag, VERSION_TAG_PATTERN } from "../../domain/biz/lib/sop";
import type { SopVersion } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  bizId: number;
  /** 業務の版（草稿も含む）。コピー元とタグの候補に使う */
  versions: SopVersion[];
  onClose: () => void;
  onCreated: (version: SopVersion) => void;
}

/** 新しい版（草稿）を作る。最新の承認済みの版を写す（無ければ空）。タグは vX.Y.Z の形 */
export function NewDraftModal({ open, bizId, versions, onClose, onCreated }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ version_tag?: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source = newDraftSource(versions);

  // 開くたびに候補のタグを入れ直す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({ version_tag: suggestNextTag(versions) });
      setError(null);
    }
  }, [open, versions, form]);

  const onFinish = async (v: { version_tag?: string }) => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await sopApi.newDraft(bizId, (v.version_tag ?? "").trim());
      message.success(`草稿 ${created.version_tag} を作りました。`);
      onClose();
      onCreated(created);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "作れませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} title="新しい版（草稿）" okText="作る" cancelText="キャンセル" confirmLoading={submitting} onOk={() => form.submit()} onCancel={onClose} forceRender>
      <Typography.Paragraph>
        {source ? (
          <>
            <span className="mono">{source.version_tag}</span>（{SOP_STATE_LABEL[source.state]}）のステップを写した草稿を作ります。
            草稿を編集して承認すると、発効日からのオーダーが新しい版を使います。
          </>
        ) : (
          "この業務にはまだ承認済みの版がありません。空の草稿を作り、ステップを追加してください。"
        )}
      </Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item
          name="version_tag"
          label="タグ"
          extra="v1.2.0 や v1.2.0-teamA の形。業務の中で重複できません。"
          rules={[
            { required: true, whitespace: true, message: "タグを入力してください" },
            { pattern: VERSION_TAG_PATTERN, message: "v1.2.0 や v1.2.0-teamA の形で入力してください" },
            { validator: (_, value: string | undefined) =>
                versions.some((x) => x.version_tag === (value ?? "").trim()) ? Promise.reject(new Error("このタグは既にあります")) : Promise.resolve() },
          ]}
        >
          <Input className="mono" maxLength={50} autoComplete="off" style={{ width: 240 }} />
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
    </Modal>
  );
}
