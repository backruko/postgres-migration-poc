import { Navigate, Route, Routes } from "react-router-dom";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { DailyDashboardPage } from "../features/daily-dashboard/DailyDashboardPage";
import { HolidaySettingsPage } from "../features/plan-settings/HolidaySettingsPage";
import { PlanSettingsPage } from "../features/plan-settings/PlanSettingsPage";
import { SheetSettingsPage } from "../features/plan-settings/SheetSettingsPage";
import { StaffSettingsPage } from "../features/staff-settings/StaffSettingsPage";
import { AppLayout } from "./AppLayout";

/**
 * 画面のルーティング
 * - /                     日々の利用: 顧客の月カレンダー（予定の変更・取消・臨時追加）
 * - /daily                日々の利用: 全顧客横断の日次ダッシュボード（参照のみ）
 * - /settings/templates   事前設定: 予定テンプレートの管理（一覧・作成・編集・削除）
 * - /settings/sheets      事前設定: 年間計画シートの参照と、ルールで日付を作るマーカー・日付の管理
 * - /settings/holidays    事前設定: 営業日カレンダーの休日の管理
 * - /settings/staff       事前設定: 職員の管理（一覧・登録・編集・ロール・有効化／利用停止）
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<CalendarPage />} />
        <Route path="daily" element={<DailyDashboardPage />} />
        <Route path="settings/templates" element={<PlanSettingsPage />} />
        <Route path="settings/sheets" element={<SheetSettingsPage />} />
        <Route path="settings/holidays" element={<HolidaySettingsPage />} />
        <Route path="settings/staff" element={<StaffSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
