import { useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";

export interface CalendarQuery {
  client: string;
  month: string;
  holidays: string | null; // 営業日カレンダーのコード（"" は「休日を表示しない」）
}

function parse(params: URLSearchParams, defaults: CalendarQuery): CalendarQuery {
  const month = params.get("month");
  return {
    client: (params.get("client") ?? defaults.client).toUpperCase(),
    month: month && /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? month : defaults.month,
    holidays: params.has("holidays") ? params.get("holidays") : defaults.holidays,
  };
}

/**
 * 顧客・月・休日カレンダーを URL のクエリ（?client=MOCK&month=2026-11&holidays=JP_BANK）と同期する。
 * 表示中のカレンダーをそのまま URL で共有できる。
 * ルーター経由で書き換えるため、画面を切り替えて戻ったときにナビゲーションがこのクエリを復元できる（AppLayout）。
 */
export function useUrlState(defaults: CalendarQuery) {
  const [params, setParams] = useSearchParams();
  // 既定値は毎回の描画で作り直されるため、update を安定させるために ref で持つ
  const defaultsRef = useRef(defaults);
  defaultsRef.current = defaults;

  const query = parse(params, defaults);

  const update = useCallback(
    (patch: Partial<CalendarQuery>) => {
      setParams(
        (prev) => {
          const next = { ...parse(prev, defaultsRef.current), ...patch };
          return new URLSearchParams({ client: next.client, month: next.month, holidays: next.holidays ?? "" });
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return [query, update] as const;
}
