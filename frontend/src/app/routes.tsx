import { Navigate, Route, Routes } from "react-router-dom";
import { BusinessSettingsPage } from "../features/business-settings/BusinessSettingsPage";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { ClientSettingsPage } from "../features/client-settings/ClientSettingsPage";
import { DailyDashboardPage } from "../features/daily-dashboard/DailyDashboardPage";
import { DependencySettingsPage } from "../features/dependency-settings/DependencySettingsPage";
import { HolidaySettingsPage } from "../features/plan-settings/HolidaySettingsPage";
import { PlanSettingsPage } from "../features/plan-settings/PlanSettingsPage";
import { SheetSettingsPage } from "../features/plan-settings/SheetSettingsPage";
import { SlaReportPage } from "../features/sla-report/SlaReportPage";
import { SopSettingsPage } from "../features/sop-settings/SopSettingsPage";
import { StaffSettingsPage } from "../features/staff-settings/StaffSettingsPage";
import { AppLayout } from "./AppLayout";

/**
 * 画面のルーティング
 * - /                     日々の利用: 顧客の月カレンダー（予定の変更・取消・臨時追加）
 * - /daily                日々の利用: 全顧客横断の日次ダッシュボード（参照のみ）
 * - /reports/sla          集計: SLA（顧客 × 締切の月の完了・遅れ・期限内率。CSV 出力）
 * - /settings/clients     事前設定: 顧客の管理（一覧・登録・顧客名の変更・無効 / 有効）
 * - /settings/businesses  事前設定: 顧客の業務の管理（一覧・登録・編集・削除・元に戻す。?client=）
 * - /settings/sop         事前設定: 業務の SOP（版の一覧・ステップ・新しい版・草稿の削除。?biz=&version=）
 * - /settings/templates   事前設定: 予定テンプレートの管理（一覧・作成・編集・削除）
 * - /settings/sheets      事前設定: 年間計画シートの参照と、ルールで日付を作るマーカー・日付の管理
 * - /settings/holidays    事前設定: 営業日カレンダーの休日の管理
 * - /settings/dependencies 事前設定: 業務の依存関係（上流 → 下流）の管理（顧客ごと）
 * - /settings/staff       事前設定: 職員の管理（一覧・登録・編集・ロール・有効化／利用停止）
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<CalendarPage />} />
        <Route path="daily" element={<DailyDashboardPage />} />
        <Route path="reports/sla" element={<SlaReportPage />} />
        <Route path="settings/clients" element={<ClientSettingsPage />} />
        <Route path="settings/businesses" element={<BusinessSettingsPage />} />
        <Route path="settings/sop" element={<SopSettingsPage />} />
        <Route path="settings/templates" element={<PlanSettingsPage />} />
        <Route path="settings/sheets" element={<SheetSettingsPage />} />
        <Route path="settings/holidays" element={<HolidaySettingsPage />} />
        <Route path="settings/dependencies" element={<DependencySettingsPage />} />
        <Route path="settings/staff" element={<StaffSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
