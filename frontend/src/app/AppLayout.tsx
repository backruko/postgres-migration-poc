import { useRef } from "react";
import { Alert, Badge, Button, Dropdown, Spin, Typography } from "antd";
import type { MenuProps } from "antd";
import { DownOutlined } from "@ant-design/icons";
import { Link, Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useSubmittedSopDraftCount } from "../domain/biz/hooks/useSopApprovals";
import { usePendingDraftCount } from "../domain/plan/hooks/useTemplateChanges";
import { templateWriteMode } from "../domain/plan/lib/templateChanges";
import { useCurrentStaff } from "../domain/staff/hooks/useStaff";
import { PasswordChangeForm } from "../features/auth/PasswordChangeForm";
import { useSession } from "../shared/hooks/useSession";
import { UserMenu } from "./UserMenu";

type Page = { path: string; label: string; end: boolean };

/** 毎日の作業で使う画面（ナビゲーションに並べる） */
const PAGES: Page[] = [
  { path: "/", label: "カレンダー", end: true },
  { path: "/daily", label: "日次ダッシュボード", end: false },
  { path: "/reports/sla", label: "SLA", end: false },
];

/** 事前設定の画面（「設定」メニューにまとめる） */
const SETTINGS_PAGES: Page[] = [
  { path: "/settings/clients", label: "顧客", end: false },
  { path: "/settings/businesses", label: "業務", end: false },
  { path: "/settings/sop", label: "SOP", end: false },
  { path: "/settings/templates", label: "予定テンプレート設定", end: false },
  { path: "/settings/approvals", label: "承認待ち", end: false },
  { path: "/settings/sheets", label: "年間計画シート", end: false },
  { path: "/settings/holidays", label: "休日カレンダー", end: false },
  { path: "/settings/dependencies", label: "業務の依存関係", end: false },
  { path: "/settings/staff", label: "職員", end: false },
];

/** 承認待ちの画面（業務管理者には件数のバッジを付ける） */
const APPROVALS_PATH = "/settings/approvals";

/** 今開いている設定の画面（無ければ undefined） */
export function currentSettingsPage(pathname: string): Page | undefined {
  return SETTINGS_PAGES.find((p) => pathname === p.path || pathname.startsWith(p.path + "/"));
}

/**
 * 全画面共通のレイアウト（上部のナビゲーション + 各画面）。ログインしていなければログイン画面へ移す（LG.3）。
 * 仮パスワードのままなら、パスワードを変えるまでほかの画面は出さない（バックエンドも 403 にする）。
 */
export function AppLayout() {
  const session = useSession();
  const me = useCurrentStaff();
  const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");

  // 各画面の選択状態（顧客・月・業務など）は URL のクエリにある。
  // 画面ごとに最後のクエリを覚えておき、切り替えて戻ったときに同じ選択で開く
  const location = useLocation();
  const lastSearch = useRef<Record<string, string>>({});
  lastSearch.current[location.pathname] = location.search;
  const to = (p: Page) => ({ pathname: p.path, search: lastSearch.current[p.path] ?? "" });

  // 承認待ちの件数（業務管理者だけ。決めた直後と 2 分ごとに読み直す）。ほかの人には出さない（読まない）。
  // テンプレートの下書きと、承認を依頼された SOP の草稿の合計（P.4）
  const isAdmin = templateWriteMode(me.data) === "direct";
  const pending = usePendingDraftCount(isAdmin);
  const pendingSop = useSubmittedSopDraftCount(isAdmin);
  const pendingCount = isAdmin ? (pending.data ?? 0) + (pendingSop.data ?? 0) : 0;

  const activeSetting = currentSettingsPage(location.pathname);
  const settingsMenu: MenuProps = {
    items: SETTINGS_PAGES.map((p) => ({
      key: p.path,
      label: (
        <Link to={to(p)}>
          {p.label}
          {p.path === APPROVALS_PATH && pendingCount > 0 && <Badge count={pendingCount} size="small" className="nav-badge" />}
        </Link>
      ),
    })),
    selectable: true,
    selectedKeys: activeSetting ? [activeSetting.path] : [],
  };

  if (session.signedOut || me.error?.status === 401) return <Navigate to="/login" state={{ from: location }} replace />;
  if (!me.data) {
    return me.error ? (
      <div className="wrap">
        <Alert type="error" showIcon message={`ログインしている職員を確認できませんでした: ${me.error.message}`} action={<Button size="small" onClick={me.reload}>再読み込み</Button>} />
      </div>
    ) : (
      <div className="login-wrap"><Spin /></div>
    );
  }
  if (me.data.must_change_password) {
    return (
      <div className="login-wrap">
        <div className="login-card">
          <div className="eyebrow">{me.data.name}（{me.data.login_id}）</div>
          <Typography.Title level={3} style={{ marginTop: 4 }}>パスワードを変更してください</Typography.Title>
          <Typography.Paragraph type="secondary">
            システム管理者が設定した仮パスワードでログインしています。自分のパスワードに変えると使えるようになります。
          </Typography.Paragraph>
          <PasswordChangeForm me={me.data} onChanged={me.reload} />
        </div>
      </div>
    );
  }

  return (
    <>
      <nav className="app-nav" aria-label="画面の切り替え">
        <div className="app-nav-inner">
          {PAGES.map((p) => (
            <NavLink key={p.path} to={to(p)} end={p.end} className={linkClass}>
              {p.label}
            </NavLink>
          ))}
          <Dropdown menu={settingsMenu} trigger={["hover", "click"]}>
            <button type="button" className={activeSetting ? "nav-link nav-menu active" : "nav-link nav-menu"} aria-haspopup="menu">
              設定{activeSetting && <span className="nav-menu-current">：{activeSetting.label}</span>}
              {pendingCount > 0 && (
                <Badge count={pendingCount} size="small" className="nav-badge" title={`承認待ち ${pendingCount} 件（予定テンプレートの下書き・SOP の草稿）`} />
              )}
              <DownOutlined className="nav-menu-caret" />
            </button>
          </Dropdown>
          <span className="nav-spacer" />
          <UserMenu me={me.data} onChanged={me.reload} />
        </div>
      </nav>
      <Outlet />
    </>
  );
}
