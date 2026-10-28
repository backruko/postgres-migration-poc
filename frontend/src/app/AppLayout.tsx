import { useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

const PAGES = [
  { path: "/", label: "カレンダー", end: true },
  { path: "/daily", label: "日次ダッシュボード", end: false },
  { path: "/settings/templates", label: "予定テンプレート設定", end: false },
  { path: "/settings/sheets", label: "年間計画シート", end: false },
  { path: "/settings/holidays", label: "休日カレンダー", end: false },
];

/** 全画面共通のレイアウト（上部のナビゲーション + 各画面） */
export function AppLayout() {
  const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");

  // 各画面の選択状態（顧客・月・業務など）は URL のクエリにある。
  // 画面ごとに最後のクエリを覚えておき、切り替えて戻ったときに同じ選択で開く
  const location = useLocation();
  const lastSearch = useRef<Record<string, string>>({});
  lastSearch.current[location.pathname] = location.search;

  return (
    <>
      <nav className="app-nav" aria-label="画面の切り替え">
        <div className="app-nav-inner">
          {PAGES.map((p) => (
            <NavLink key={p.path} to={{ pathname: p.path, search: lastSearch.current[p.path] ?? "" }} end={p.end} className={linkClass}>
              {p.label}
            </NavLink>
          ))}
        </div>
      </nav>
      <Outlet />
    </>
  );
}
