import { useCallback, useState } from "react";

export interface CalendarQuery {
  client: string;
  month: string;
  holidays: string | null; // 営業日カレンダーのコード（"" は「休日を表示しない」）
}

/**
 * 顧客・月・休日カレンダーを URL のクエリ（?client=MOCK&month=2026-11&holidays=JP_BANK）と同期する。
 * 表示中のカレンダーをそのまま URL で共有できる。
 */
export function useUrlState(defaults: CalendarQuery) {
  const [query, setQuery] = useState<CalendarQuery>(() => {
    const params = new URLSearchParams(window.location.search);
    const month = params.get("month");
    return {
      client: (params.get("client") ?? defaults.client).toUpperCase(),
      month: month && /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? month : defaults.month,
      holidays: params.has("holidays") ? params.get("holidays") : defaults.holidays,
    };
  });

  const update = useCallback((patch: Partial<CalendarQuery>) => {
    setQuery((prev) => {
      const next = { ...prev, ...patch };
      const params = new URLSearchParams({ client: next.client, month: next.month, holidays: next.holidays ?? "" });
      window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
      return next;
    });
  }, []);

  return [query, update] as const;
}
