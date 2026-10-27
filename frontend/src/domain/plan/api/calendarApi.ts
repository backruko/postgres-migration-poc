import { http, unwrap } from "../../../shared/api/http";
import type { BusinessCalendar, ClientCalendar } from "../types";

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
};
