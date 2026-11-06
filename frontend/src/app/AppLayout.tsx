import { useRef } from "react";
import { Dropdown } from "antd";
import type { MenuProps } from "antd";
import { DownOutlined } from "@ant-design/icons";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { ActingStaffPicker } from "./ActingStaffPicker";

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
  { path: "/settings/templates", label: "予定テンプレート設定", end: false },
  { path: "/settings/sheets", label: "年間計画シート", end: false },
  { path: "/settings/holidays", label: "休日カレンダー", end: false },
  { path: "/settings/dependencies", label: "業務の依存関係", end: false },
  { path: "/settings/staff", label: "職員", end: false },
];

/** 今開いている設定の画面（無ければ undefined） */
export function currentSettingsPage(pathname: string): Page | undefined {
  return SETTINGS_PAGES.find((p) => pathname === p.path || pathname.startsWith(p.path + "/"));
}

/** 全画面共通のレイアウト（上部のナビゲーション + 各画面） */
export function AppLayout() {
  const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");

  // 各画面の選択状態（顧客・月・業務など）は URL のクエリにある。
  // 画面ごとに最後のクエリを覚えておき、切り替えて戻ったときに同じ選択で開く
  const location = useLocation();
  const lastSearch = useRef<Record<string, string>>({});
  lastSearch.current[location.pathname] = location.search;
  const to = (p: Page) => ({ pathname: p.path, search: lastSearch.current[p.path] ?? "" });

  const activeSetting = currentSettingsPage(location.pathname);
  const settingsMenu: MenuProps = {
    items: SETTINGS_PAGES.map((p) => ({ key: p.path, label: <Link to={to(p)}>{p.label}</Link> })),
    selectable: true,
    selectedKeys: activeSetting ? [activeSetting.path] : [],
  };

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
              <DownOutlined className="nav-menu-caret" />
            </button>
          </Dropdown>
          <span className="nav-spacer" />
          <ActingStaffPicker />
        </div>
      </nav>
      <Outlet />
    </>
  );
}
