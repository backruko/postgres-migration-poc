// 休日の入力（貼り付けた一覧の読み取り・API の入力への変換）と結果の表示。UI から独立した純粋な処理。
import { japaneseHolidays } from "../../domain/plan/lib/jpHolidays";
import type { CalendarChangeResult, HolidayInput } from "../../domain/plan/types";
import { dayjs } from "../../shared/lib/datetime";

export interface HolidayRow {
  /** YYYY-MM-DD */
  date: string;
  name?: string;
}

const DATE_FORMATS = ["YYYY-MM-DD", "YYYY/M/D", "YYYY/MM/DD", "YYYY-M-D"];

/** "2027-01-01" "2027/1/1" などを YYYY-MM-DD に。読めない・存在しない日付は null */
export function parseDate(text: string): string | null {
  const d = dayjs(text.trim(), DATE_FORMATS, true);
  return d.isValid() ? d.format("YYYY-MM-DD") : null;
}

/**
 * 貼り付けた休日の一覧を読み取る。1行に「日付 名前」（区切りは空白・タブ・カンマ）。名前は省略できる。
 * 例: "2027-01-01 元日" / "2027/1/11\t成人の日" / "2027-02-11,建国記念の日"
 * 空行は無視し、日付として読めない行は errors に入れる（行番号は1始まり）。
 */
export function parseHolidayLines(text: string): { rows: HolidayRow[]; errors: { line: number; text: string }[] } {
  const rows: HolidayRow[] = [];
  const errors: { line: number; text: string }[] = [];
  text.split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    const m = /^(\S+?)(?:[\s,、]+(.*))?$/.exec(line);
    const date = m ? parseDate(m[1]) : null;
    if (!date) {
      errors.push({ line: i + 1, text: line });
      return;
    }
    const name = m?.[2]?.trim();
    rows.push(name ? { date, name } : { date });
  });
  return { rows, errors };
}

/** API の入力に（名前は前後の空白を除き、空なら null。日付順） */
export function toHolidayInputs(rows: HolidayRow[]): HolidayInput[] {
  return [...rows]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((r) => ({ holiday_date: r.date, name: r.name?.trim() || null }));
}

/** 今日より前の休日は実績のため変更しない（過去の予定は作り直されず、表示だけが実際と食い違うため） */
export const isEditableHoliday = (date: string, today: string) => date >= today;

/** 追加・名前変更・削除の結果を、利用者向けの文に */
export function describeChange(result: CalendarChangeResult): string {
  const parts = [
    result.holidays_added ? `休日を ${result.holidays_added} 件追加` : "",
    result.holidays_renamed ? `${result.holidays_renamed} 件の名前を変更` : "",
    result.holidays_deleted ? `休日を ${result.holidays_deleted} 件削除` : "",
  ].filter(Boolean);
  const head = parts.length ? `${parts.join("、")}しました` : "変更はありませんでした";
  return result.templates_regenerated
    ? `${head}（このカレンダーを使う ${result.templates_regenerated} 件のテンプレートの今日以降の予定を作り直しました）`
    : head;
}

/**
 * 日本の祝日の取り込み内容。今日より前（実績）と登録済みの日付は取り込まない（登録済みの名前も変えない）。
 * 対象外の年（jpHolidays の JP_HOLIDAY_YEARS 以外）は例外。
 */
export function planJpHolidayImport(year: number, existing: ReadonlySet<string>, today: string) {
  const all = japaneseHolidays(year);
  const toAdd: HolidayRow[] = [];
  let past = 0;
  let registered = 0;
  for (const h of all) {
    if (!isEditableHoliday(h.date, today)) past++;
    else if (existing.has(h.date)) registered++;
    else toAdd.push({ date: h.date, name: h.name });
  }
  return { total: all.length, toAdd, past, registered };
}
