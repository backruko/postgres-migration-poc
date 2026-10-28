import { useApiQuery } from "../../../shared/hooks/useApiQuery";
import { calendarApi } from "../api";

/** 営業日カレンダーの全休日（年ごとの表示は画面で絞る。追加時に登録済みの日付を判定するため全件を持つ）。未選択の間は取得しない */
export function useHolidays(calendarId: number | null) {
  return useApiQuery((signal) => calendarApi.listHolidays(calendarId as number, null, signal), [calendarId], {
    enabled: calendarId !== null,
  });
}
