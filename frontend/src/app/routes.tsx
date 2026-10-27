import { Navigate, Route, Routes } from "react-router-dom";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { PlanSettingsPage } from "../features/plan-settings/PlanSettingsPage";
import { AppLayout } from "./AppLayout";

/**
 * 画面のルーティング
 * - /                     日々の利用: 顧客の月カレンダー（予定の変更・取消・臨時追加）
 * - /settings/templates   事前設定: 予定テンプレートの管理（現在は一覧の参照のみ）
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<CalendarPage />} />
        <Route path="settings/templates" element={<PlanSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
