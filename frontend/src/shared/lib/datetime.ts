import dayjs, { type Dayjs } from "dayjs";
import "dayjs/locale/ja";
import customParseFormat from "dayjs/plugin/customParseFormat";
import timezone from "dayjs/plugin/timezone";
import utc from "dayjs/plugin/utc";

// customParseFormat: "HH:mm:ss" などの書式指定での解析（テンプレートの start_time 等）に必要
dayjs.extend(customParseFormat);
dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.locale("ja");

export { dayjs, type Dayjs };

export const WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]; // ISO 1..7 の順

/** ISO 曜日（1=月 ... 7=日）を日本語の1文字に */
export const weekdayJa = (isoWeekday: number) => WEEKDAY_JA[isoWeekday - 1];

// ---------------- 月の計算（"YYYY-MM" 文字列で扱う） ----------------
export function addMonths(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const index = y * 12 + (m - 1) + delta;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

export const monthOf = (isoDate: string) => isoDate.slice(0, 7);

export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${y}年${m}月`;
}

/** 指定タイムゾーンでの「今日」（YYYY-MM-DD） */
export const todayIn = (tz: string) => dayjs().tz(tz).format("YYYY-MM-DD");

/** タイムゾーン付きの日時を、指定タイムゾーンの dayjs に */
export const inZone = (iso: string, tz: string) => dayjs(iso).tz(tz);

/** タイムゾーン付きの日時を、指定タイムゾーンの HH:mm に */
export const timeIn = (iso: string, tz: string) => inZone(iso, tz).format("HH:mm");

/**
 * 画面で選んだ「日付」と「時刻」を、指定タイムゾーンの壁時計時刻として ISO 8601（オフセット付き）にする。
 * ブラウザのタイムゾーンに関係なく、例: 2026-11-10 + 09:00 + Asia/Tokyo → 2026-11-10T09:00:00+09:00
 */
export function zonedISO(date: Dayjs, time: Dayjs, tz: string): string {
  return dayjs.tz(`${date.format("YYYY-MM-DD")}T${time.format("HH:mm")}:00`, tz).format();
}
