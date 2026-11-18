import { Alert, App, Input, Modal, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { sopApi } from "../../domain/biz/api/sopApi";
import type { SopVersion } from "../../domain/biz/types";
import { ApiError } from "../../shared/api/http";

interface Props {
  open: boolean;
  /** 差し戻す草稿（閉じる間の表示が消えないよう、閉じても渡したままにする） */
  draft: SopVersion | null;
  onClose: () => void;
  onRejected: () => void;
}

/**
 * 依頼された SOP の草稿の差し戻し（業務管理者。P.4）。理由は必須で、版の記録に残り、書いた人が履歴で読める。
 * 差し戻すと草稿に戻り、また直せる。
 */
export function RejectDraftModal({ open, draft, onClose, onRejected }: Props) {
  const { message } = App.useApp();
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setReason("");
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  const reject = async () => {
    if (!draft) return;
    setSubmitting(true);
    setError(null);
    try {
      await sopApi.reject(draft.id, reason);
      message.success(`${draft.version_tag} を差し戻しました。`);
      onClose();
      onRejected();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "差し戻せませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={draft ? `${draft.version_tag} を差し戻す` : "差し戻す"}
      okText="差し戻す"
      okButtonProps={{ danger: true, disabled: !reason.trim() }}
      cancelText="キャンセル"
      confirmLoading={submitting}
      onOk={() => void reject()}
      onCancel={onClose}
      destroyOnClose
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text>草稿に戻り、書いた人がまた直せるようになります。理由は版の履歴に残ります。</Typography.Text>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">差し戻す理由（必須）</span>
          <Input.TextArea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoSize={{ minRows: 2, maxRows: 6 }} />
        </label>
        {error && <Alert type="error" showIcon message={error} />}
      </Space>
    </Modal>
  );
}
