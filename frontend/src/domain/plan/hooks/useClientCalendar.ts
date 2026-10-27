import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { calendarApi } from "../api";

/** 顧客の月カレンダー。顧客コードが空の間は取得しない。存在しない顧客（404）の場合は表示を消す */
export function useClientCalendar(clientCode: string, month: string, timezone: string, holidayCalendar: string | null) {
  return useApiQuery(
    (signal) => calendarApi.getClientCalendar(clientCode, month, { timezone, holidayCalendar }, signal),
    [clientCode, month, timezone, holidayCalendar],
    { enabled: Boolean(clientCode), keepDataOnError: (e) => e.status !== 404 },
  );
}

/** 営業日カレンダーの一覧（休日表示の切り替え用） */
export function useBusinessCalendars() {
  return useApiQuery((signal) => calendarApi.listBusinessCalendars(signal), []);
}
