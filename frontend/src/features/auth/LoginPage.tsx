import { Alert, Button, Form, Input, Typography } from "antd";
import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { authApi } from "../../domain/staff/api";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { loginErrorText } from "../../domain/staff/lib/login";
import { markSignedIn } from "../../shared/api/session";
import { useSession } from "../../shared/hooks/useSession";

interface Values {
  login_id?: string;
  password?: string;
}

/** ログインのあとに戻るページ（ログイン画面へ移されたときのページ。無ければカレンダー） */
const returnPath = (state: unknown) => {
  const from = (state as { from?: { pathname: string; search?: string } } | null)?.from;
  return from && from.pathname !== "/login" ? `${from.pathname}${from.search ?? ""}` : "/";
};

/**
 * ログイン画面（LG.3）。ログイン ID とパスワード。成功すると元のページへ戻る
 * （仮パスワードなら、戻った先でパスワードの変更を求められる）。
 */
export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useSession();
  const me = useCurrentStaff();
  const [form] = Form.useForm<Values>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // すでにログインしている（別のタブでログインした・戻るボタン）ならそのまま戻す
  if (me.data && !session.signedOut) return <Navigate to={returnPath(location.state)} replace />;

  const onFinish = async (v: Values) => {
    setSubmitting(true);
    setError(null);
    try {
      await authApi.login({ login_id: (v.login_id ?? "").trim(), password: v.password ?? "" });
      markSignedIn();
      navigate(returnPath(location.state), { replace: true });
    } catch (e) {
      setError(loginErrorText(e));
      form.setFieldValue("password", "");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="eyebrow">オペレーション管理</div>
        <Typography.Title level={3} style={{ marginTop: 4 }}>ログイン</Typography.Title>
        <Form<Values> form={form} layout="vertical" onFinish={onFinish} requiredMark={false} disabled={submitting}>
          <Form.Item name="login_id" label="ログインID" rules={[{ required: true, whitespace: true, message: "ログインIDを入力してください" }]}>
            <Input autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item name="password" label="パスワード" rules={[{ required: true, message: "パスワードを入力してください" }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
          <Button type="primary" htmlType="submit" block loading={submitting}>
            ログイン
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 12 }}>
          パスワードを忘れたときは、システム管理者に仮パスワードを設定してもらってください。
        </Typography.Paragraph>
      </div>
    </div>
  );
}
