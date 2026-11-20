import { Navigate, Route, Routes } from "react-router";
import { LoginPage } from "../features/auth/LoginPage";
import { BusinessSettingsPage } from "../features/business-settings/BusinessSettingsPage";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { ClientSettingsPage } from "../features/client-settings/ClientSettingsPage";
import { DailyDashboardPage } from "../features/daily-dashboard/DailyDashboardPage";
import { EventLogPage } from "../features/event-log/EventLogPage";
import { DependencySettingsPage } from "../features/dependency-settings/DependencySettingsPage";
import { ApprovalsPage } from "../features/plan-settings/ApprovalsPage";
import { HolidaySettingsPage } from "../features/plan-settings/HolidaySettingsPage";
import { PlanSettingsPage } from "../features/plan-settings/PlanSettingsPage";
import { SheetSettingsPage } from "../features/plan-settings/SheetSettingsPage";
import { NotificationSettingsPage } from "../features/notification-settings/NotificationSettingsPage";
import { SlaReportPage } from "../features/sla-report/SlaReportPage";
import { SopSettingsPage } from "../features/sop-settings/SopSettingsPage";
import { StaffSettingsPage } from "../features/staff-settings/StaffSettingsPage";
import { WorkerHealthPage } from "../features/worker-health/WorkerHealthPage";
import { AppLayout } from "./AppLayout";

/**
 * 画面のルーティング（/login 以外はログインが必要。AppLayout がログイン画面へ移す）
 * - /login               ログイン（ログイン ID・パスワード）
 * - /                     日々の利用: 顧客の月カレンダー（予定の変更・取消・臨時追加）
 * - /daily                日々の利用: 全顧客横断の日次ダッシュボード（参照のみ）
 * - /reports/sla          集計: SLA（顧客 × 締切の月の完了・遅れ・期限内率。CSV 出力）
 * - /reports/events       確認: 記録された出来事と通知（タブ: 出来事 / 通知の記録。参照のみ）
 * - /reports/worker       確認: ワーカーの状態（いまの状態・1 日ごとの心拍とエラー・期間の判定。参照のみ。?from=&to=）
 * - /settings/clients     事前設定: 顧客の管理（一覧・登録・顧客名の変更・無効 / 有効）
 * - /settings/businesses  事前設定: 顧客の業務の管理（一覧・登録・編集・削除・元に戻す。?client=）
 * - /settings/sop         事前設定: 業務の SOP（版の一覧・ステップ・新しい版・草稿の削除。?biz=&version=）
 * - /settings/templates   事前設定: 予定テンプレートの管理（一覧・作成・編集・削除。リリース担当は下書き）
 * - /settings/approvals   事前設定: 承認待ちのテンプレートの下書き（全業務。承認・差し戻しは業務管理者。?client=）
 * - /settings/sheets      事前設定: 年間計画シートの参照と、ルールで日付を作るマーカー・日付の管理
 * - /settings/holidays    事前設定: 営業日カレンダーの休日の管理
 * - /settings/dependencies 事前設定: 業務の依存関係（上流 → 下流）の管理（顧客ごと）
 * - /settings/staff       事前設定: 職員の管理（一覧・登録・編集・ロール・有効化／利用停止）
 * - /settings/notifications 事前設定: 通知の設定（ルール・宛先・テスト送信。?tab=）
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppLayout />}>
        <Route index element={<CalendarPage />} />
        <Route path="daily" element={<DailyDashboardPage />} />
        <Route path="reports/sla" element={<SlaReportPage />} />
        <Route path="reports/events" element={<EventLogPage />} />
        <Route path="reports/worker" element={<WorkerHealthPage />} />
        <Route path="settings/clients" element={<ClientSettingsPage />} />
        <Route path="settings/businesses" element={<BusinessSettingsPage />} />
        <Route path="settings/sop" element={<SopSettingsPage />} />
        <Route path="settings/templates" element={<PlanSettingsPage />} />
        <Route path="settings/approvals" element={<ApprovalsPage />} />
        <Route path="settings/sheets" element={<SheetSettingsPage />} />
        <Route path="settings/holidays" element={<HolidaySettingsPage />} />
        <Route path="settings/dependencies" element={<DependencySettingsPage />} />
        <Route path="settings/staff" element={<StaffSettingsPage />} />
        <Route path="settings/notifications" element={<NotificationSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
