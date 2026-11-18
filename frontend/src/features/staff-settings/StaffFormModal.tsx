import { Alert, App, Checkbox, Form, Input, Modal, Select, Typography } from "antd";
import { useEffect, useState } from "react";
import { useClients } from "../../domain/client/hooks/useClients";
import { staffApi } from "../../domain/staff/api";
import { canHaveClientScope } from "../../domain/staff/lib/permissions";
import type { RoleCode, Role, Staff } from "../../domain/staff/types";
import { ApiError } from "../../shared/api/http";
import {
  clientOptions, clientsChanged, lockedRoles, machineOnlyDisabled, rolesChanged, saveOrder, toCreateInput, toUpdateInput,
  type StaffFormValues,
} from "./staffForm";

interface Props {
  open: boolean;
  /** 編集する職員。null なら新規登録 */
  staff: Staff | null;
  me: Staff | null;
  roles: Role[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 職員の登録と編集（氏名・メールアドレス・ロール・担当顧客）。
 * 登録した職員は「利用開始前」で作られ、一覧の「有効化」で使えるようになる。ログインIDは登録後に変えられない。
 * 担当顧客を選ぶと、その顧客だけを扱う職員になる（空 = 全顧客）。システム管理者には付けられない。
 */
export function StaffFormModal({ open, staff, me, roles, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<StaffFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = lockedRoles(staff, me);
  const clients = useClients();
  const selectedRoles = Form.useWatch("roles", form) as RoleCode[] | undefined;
  const scopeAllowed = canHaveClientScope(selectedRoles ?? []);
  // システム連携（読み取りの API キー）は機械用のアカウントだけ: 人のロールと一緒には選べない
  const machineDisabled = machineOnlyDisabled(selectedRoles ?? [], roles.map((r) => r.code));
  const isAgent = (selectedRoles ?? []).includes("system_agent");

  // システム管理者を選んだら担当顧客を外す（システム管理者は全顧客を扱う）
  useEffect(() => {
    if (!scopeAllowed && (form.getFieldValue("client_codes") ?? []).length) form.setFieldValue("client_codes", []);
  }, [scopeAllowed, form]);

  // 開くたびに初期値へ戻す（forceRender でフォームは閉じても残るため）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(
        staff
          ? { login_id: staff.login_id, name: staff.name, email: staff.email ?? "", roles: staff.roles, client_codes: staff.client_codes }
          : { roles: [], client_codes: [] },
      );
      setError(null);
    }
  }, [open, staff, form]);

  const onFinish = async (v: StaffFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      const nextClients = v.client_codes ?? [];
      if (staff === null) {
        const created = await staffApi.create(toCreateInput(v));
        if (nextClients.length) await staffApi.setClients(created.id, nextClients);
        message.success(`「${created.name}」を登録しました（利用開始前）。一覧の「有効化」で使えるようになります。`, 6);
      } else {
        const update = toUpdateInput(staff, v);
        if (update) await staffApi.update(staff.id, update);
        const nextRoles = v.roles ?? [];
        const roleChange = rolesChanged(staff, nextRoles);
        const clientChange = clientsChanged(staff.client_codes, nextClients);
        // システム管理者と担当顧客は両立しないので、外す側を先に保存する
        const saveRoles = () => (roleChange ? staffApi.setRoles(staff.id, nextRoles) : Promise.resolve(null));
        const saveClients = () => (clientChange ? staffApi.setClients(staff.id, nextClients) : Promise.resolve(null));
        if (saveOrder(nextClients) === "clients-first") {
          await saveClients();
          await saveRoles();
        } else {
          await saveRoles();
          await saveClients();
        }
        message.success(update || roleChange || clientChange ? `「${(v.name ?? "").trim()}」を保存しました。` : "変更はありませんでした。");
      }
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
      onSaved(); // 氏名だけ保存されてロールで失敗した場合も一覧を最新にする
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={staff ? `「${staff.name}」を編集` : "職員を登録"}
      okText={staff ? "保存する" : "登録する"}
      cancelText="キャンセル"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={submitting}
      width={560}
      forceRender
    >
      {!staff && (
        <Typography.Paragraph type="secondary" className="modal-note">
          登録した職員は「利用開始前」になります。一覧の「有効化」で使えるようになります。
        </Typography.Paragraph>
      )}

      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        <Form.Item
          name="login_id"
          label="ログインID"
          extra={staff ? "ログインIDは変更できません。" : "社員番号やメールアドレスなど、職員ごとに一意な値"}
          rules={[{ required: true, whitespace: true, message: "ログインIDを入力してください" }, { max: 100 }]}
        >
          <Input maxLength={100} disabled={staff !== null} autoComplete="off" />
        </Form.Item>
        <Form.Item name="name" label="氏名" rules={[{ required: true, whitespace: true, message: "氏名を入力してください" }, { max: 100 }]}>
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="email" label="メールアドレス" rules={[{ type: "email", message: "メールアドレスの形式で入力してください" }, { max: 255 }]}>
          <Input maxLength={255} autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="roles"
          label="ロール"
          extra={
            locked.length
              ? "自分のシステム管理者ロールは外せません（管理者がいなくなるのを防ぐため）。"
              : isAgent
                ? "システム連携（読み取り）は機械用のアカウントです。ほかに選べるのは「信号の送信」だけで、パスワードは設定できません（API キーで読むだけ）。"
                : "複数選べます。どの顧客を扱えるかは、下の担当顧客で決まります。"
          }
        >
          <Checkbox.Group className="role-checks">
            {roles.map((r) => (
              <Checkbox key={r.code} value={r.code} disabled={locked.includes(r.code) || machineDisabled.includes(r.code)}>
                <span className="role-check-name">{r.name}</span>
                {r.description && <span className="muted role-check-desc">{r.description}</span>}
              </Checkbox>
            ))}
          </Checkbox.Group>
        </Form.Item>
        <Form.Item
          name="client_codes"
          label="担当顧客"
          extra={
            scopeAllowed
              ? "空のままなら全顧客を扱えます。選ぶと、その顧客の業務・予定・オーダーだけが見え、顧客・休日カレンダー・職員の設定は変えられなくなります。"
              : "システム管理者は全顧客を扱います（担当顧客は付けられません）。"
          }
        >
          <Select
            mode="multiple"
            allowClear
            placeholder="全顧客"
            disabled={!scopeAllowed}
            loading={clients.loading}
            options={clientOptions(clients.data ?? [], staff?.client_codes ?? [])}
            optionFilterProp="label"
            className="client-scope-select"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
