import { http, unwrap } from "../../../shared/api/http";
import type { BusinessCalendar, CalendarChangeResult, ClientCalendar, Holiday, HolidayInput } from "../types";

export const calendarApi = {
  /** 顧客に属する全業務の予定を、月カレンダーの形で取得する（GET /plans/clients/{client_code}/calendar） */
  getClientCalendar(
    clientCode: string,
    month: string,
    options: { timezone?: string; holidayCalendar?: string | null } = {},
    signal?: AbortSignal,
  ): Promise<ClientCalendar> {
    return unwrap(
      http.GET("/plans/clients/{client_code}/calendar", {
        params: {
          path: { client_code: clientCode },
          query: { month, timezone: options.timezone, holiday_calendar: options.holidayCalendar || undefined },
        },
        signal,
      }),
    );
  },

  /** 営業日カレンダーの一覧（GET /plans/calendars） */
  listBusinessCalendars(signal?: AbortSignal): Promise<BusinessCalendar[]> {
    return unwrap(http.GET("/plans/calendars", { signal }));
  },

  /** 営業日カレンダーの休日（年を指定するとその年のみ）（GET /plans/calendars/{id}/holidays） */
  listHolidays(calendarId: number, year: number | null, signal?: AbortSignal): Promise<Holiday[]> {
    return unwrap(
      http.GET("/plans/calendars/{calendar_id}/holidays", {
        params: { path: { calendar_id: calendarId }, query: { year: year ?? undefined } },
        signal,
      }),
    );
  },

  /**
   * 休日を一括登録する（登録済みの日付は名前だけ更新）（POST /plans/calendars/{id}/holidays）。
   * 休日が増えると、このカレンダーを使うテンプレートと生成マーカーの今日以降の日付が作り直される。
   */
  addHolidays(calendarId: number, holidays: HolidayInput[]): Promise<CalendarChangeResult> {
    return unwrap(
      http.POST("/plans/calendars/{calendar_id}/holidays", { params: { path: { calendar_id: calendarId } }, body: { holidays } }),
    );
  },

  /** 休日を削除する（その日は営業日に戻り、今日以降の日付が作り直される）（DELETE /plans/calendars/{id}/holidays/{date}） */
  deleteHoliday(calendarId: number, holidayDate: string): Promise<CalendarChangeResult> {
    return unwrap(
      http.DELETE("/plans/calendars/{calendar_id}/holidays/{holiday_date}", {
        params: { path: { calendar_id: calendarId, holiday_date: holidayDate } },
      }),
    );
  },
};
