import { Navigate, Route, Routes } from "react-router-dom";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { PlanSettingsPage } from "../features/plan-settings/PlanSettingsPage";
import { SheetSettingsPage } from "../features/plan-settings/SheetSettingsPage";
import { AppLayout } from "./AppLayout";

/**
 * 画面のルーティング
 * - /                     日々の利用: 顧客の月カレンダー（予定の変更・取消・臨時追加）
 * - /settings/templates   事前設定: 予定テンプレートの管理（一覧・作成・編集・削除）
 * - /settings/sheets      事前設定: 年間計画シートの参照と、ルールで日付を作るマーカーの管理
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<CalendarPage />} />
        <Route path="settings/templates" element={<PlanSettingsPage />} />
        <Route path="settings/sheets" element={<SheetSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
