import { NavLink, Outlet } from "react-router-dom";

/** 全画面共通のレイアウト（上部のナビゲーション + 各画面） */
export function AppLayout() {
  const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-link active" : "nav-link");

  return (
    <>
      <nav className="app-nav" aria-label="画面の切り替え">
        <div className="app-nav-inner">
          <NavLink to="/" end className={linkClass}>
            カレンダー
          </NavLink>
          <NavLink to="/settings/templates" className={linkClass}>
            予定テンプレート設定
          </NavLink>
        </div>
      </nav>
      <Outlet />
    </>
  );
}
