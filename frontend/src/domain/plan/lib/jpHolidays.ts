/**
 * 日本の国民の祝日（「国民の祝日に関する法律」の現行の規定）を計算する。外部の API・ライブラリは使わない。
 * - 春分・秋分の日は、1980〜2099年に使える近似式で求める（正式には前年2月の官報で決まる）
 * - 振替休日: 祝日が日曜日のときは、その後の最初の祝日でない日
 * - 国民の休日: 前日と翌日が祝日の日（例: 2026年9月22日）
 * 2020・2021年は東京五輪で祝日が移動したため対象外。法改正があった場合は、取り込んだ後に手で直す。
 */

export interface JpHoliday {
  /** YYYY-MM-DD */
  date: string;
  name: string;
}

/** 計算できる年（現行の規定どおりの年） */
export const JP_HOLIDAY_YEARS = { min: 2022, max: 2099 } as const;

export const isSupportedJpHolidayYear = (year: number) =>
  Number.isInteger(year) && year >= JP_HOLIDAY_YEARS.min && year <= JP_HOLIDAY_YEARS.max;

const iso = (y: number, m: number, d: number) => new Date(Date.UTC(y, m - 1, d)).toISOString().slice(0, 10);
const weekday = (date: string) => new Date(`${date}T00:00:00Z`).getUTCDay(); // 0=日
const addDays = (date: string, n: number) => {
  const t = new Date(`${date}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
};

/** その月の第 n 月曜日（ハッピーマンデー） */
function nthMonday(y: number, m: number, n: number): string {
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const day = 1 + ((8 - first) % 7) + (n - 1) * 7;
  return iso(y, m, day);
}

const vernalEquinoxDay = (y: number) => Math.floor(20.8431 + 0.242194 * (y - 1980) - Math.floor((y - 1980) / 4));
const autumnalEquinoxDay = (y: number) => Math.floor(23.2488 + 0.242194 * (y - 1980) - Math.floor((y - 1980) / 4));

/** その年の祝日（振替休日・国民の休日を含む、日付順）。対象外の年は例外 */
export function japaneseHolidays(year: number): JpHoliday[] {
  if (!isSupportedJpHolidayYear(year)) {
    throw new RangeError(`Japanese holidays can be calculated for ${JP_HOLIDAY_YEARS.min}-${JP_HOLIDAY_YEARS.max}, not ${year}.`);
  }
  const base: JpHoliday[] = [
    { date: iso(year, 1, 1), name: "元日" },
    { date: nthMonday(year, 1, 2), name: "成人の日" },
    { date: iso(year, 2, 11), name: "建国記念の日" },
    { date: iso(year, 2, 23), name: "天皇誕生日" },
    { date: iso(year, 3, vernalEquinoxDay(year)), name: "春分の日" },
    { date: iso(year, 4, 29), name: "昭和の日" },
    { date: iso(year, 5, 3), name: "憲法記念日" },
    { date: iso(year, 5, 4), name: "みどりの日" },
    { date: iso(year, 5, 5), name: "こどもの日" },
    { date: nthMonday(year, 7, 3), name: "海の日" },
    { date: iso(year, 8, 11), name: "山の日" },
    { date: nthMonday(year, 9, 3), name: "敬老の日" },
    { date: iso(year, 9, autumnalEquinoxDay(year)), name: "秋分の日" },
    { date: nthMonday(year, 10, 2), name: "スポーツの日" },
    { date: iso(year, 11, 3), name: "文化の日" },
    { date: iso(year, 11, 23), name: "勤労感謝の日" },
  ];
  const isBase = new Set(base.map((h) => h.date));

  // 国民の休日: 前日と翌日が祝日で、その日自体は祝日でも日曜日でもない日
  const citizens: JpHoliday[] = [];
  for (const h of base) {
    const next = addDays(h.date, 1);
    if (!isBase.has(next) && isBase.has(addDays(h.date, 2)) && weekday(next) !== 0) {
      citizens.push({ date: next, name: "国民の休日" });
    }
  }
  const taken = new Set([...isBase, ...citizens.map((h) => h.date)]);

  // 振替休日: 日曜日の祝日の後の、最初の祝日でない日
  const substitutes: JpHoliday[] = [];
  for (const h of base) {
    if (weekday(h.date) !== 0) continue;
    let d = addDays(h.date, 1);
    while (taken.has(d)) d = addDays(d, 1);
    taken.add(d);
    substitutes.push({ date: d, name: "振替休日" });
  }

  return [...base, ...citizens, ...substitutes].sort((a, b) => a.date.localeCompare(b.date));
}
