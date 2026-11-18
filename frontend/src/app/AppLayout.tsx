import { useRef } from "react";
import { Alert, Badge, Button, Dropdown, Spin, Typography } from "antd";
import type { MenuProps } from "antd";
import { DownOutlined } from "@ant-design/icons";
import { Link, Navigate, NavLink, Outlet, useLocation } from "react-router";
import { useSubmittedSopDraftCount } from "../domain/biz/hooks/useSopApprovals";
import { useNotificationFailures } from "../domain/notifications/hooks/useNotificationFailures";
import { usePendingDraftCount } from "../domain/plan/hooks/useTemplateChanges";
import { useWorkerHealth } from "../domain/worker/hooks/useWorkerHealth";
import { templateWriteMode } from "../domain/plan/lib/templateChanges";
import { useCurrentStaff } from "../domain/staff/hooks/useStaff";
import { isClientLimited } from "../domain/staff/lib/permissions";
import { PasswordChangeForm } from "../features/auth/PasswordChangeForm";
import { useSession } from "../shared/hooks/useSession";
import { UserMenu } from "./UserMenu";

type Page = { path: string; label: string; end: boolean };

/** 毎日の作業で使う画面（ナビゲーションに並べる） */
const PAGES: Page[] = [
  { path: "/", label: "カレンダー", end: true },
  { path: "/daily", label: "日次ダッシュボード", end: false },
  { path: "/reports/sla", label: "SLA", end: false },
  { path: "/reports/events", label: "出来事・通知", end: false },
  { path: "/reports/worker", label: "ワーカーの状態", end: false },
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
  { path: "/settings/notifications", label: "通知の設定", end: false },
  { path: "/settings/staff", label: "職員", end: false },
];

/** 職員の画面（全顧客に共通: 担当顧客が限られた職員には出さない。バックエンドも 403 にする） */
const STAFF_PATH = "/settings/staff";

/** 承認待ちの画面（業務管理者には件数のバッジを付ける） */
const APPROVALS_PATH = "/settings/approvals";

/** 日次ダッシュボード（届かなかったアラームがあれば、どの画面からも見える赤い件数を付ける。N.4b-2） */
const DAILY_PATH = "/daily";

/** ワーカーの状態（動いていなければ、どの画面からも見える赤い印を付ける。P.4c） */
const WORKER_PATH = "/reports/worker";

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
  // 届かなかったアラームのうち、まだ確認されていないもの（有効な職員だけ読む。2 分ごと・「確認した」の直後に読み直す）
  const failures = useNotificationFailures(me.data?.status === "active");
  const failureCount = me.data?.status === "active" ? (failures.data?.unacknowledged ?? 0) : 0;
  // ワーカーが動いているか（ログインしている間、1 分ごとに読み直す）。動いていなければナビゲーションに赤い印
  const worker = useWorkerHealth(Boolean(me.data));
  const workerDown = Boolean(worker.data && !worker.data.ok);

  const activeSetting = currentSettingsPage(location.pathname);
  const settingsMenu: MenuProps = {
    items: SETTINGS_PAGES.filter((p) => !(p.path === STAFF_PATH && isClientLimited(me.data))).map((p) => ({
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
              {p.path === DAILY_PATH && failureCount > 0 && (
                <Badge count={failureCount} size="small" className="nav-badge" title={`届かなかったアラーム ${failureCount} 件`} />
              )}
              {p.path === WORKER_PATH && workerDown && <Badge status="error" className="nav-dot" title="ワーカーが動いていません" />}
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
