import type { CalendarTemplateInfo, HolidayPolicy, InstanceOrigin } from "../types";

export const POLICY_LABEL: Record<HolidayPolicy, string> = {
  as_is: "休日も実施",
  skip: "休日はスキップ",
  following: "休日は翌営業日",
  preceding: "休日は前営業日",
  modified_following: "休日は翌営業日（月をまたぐ場合は前営業日）",
  modified_preceding: "休日は前営業日（月をまたぐ場合は翌営業日）",
};

export const ORIGIN_LABEL: Record<InstanceOrigin, string | null> = {
  generated: null,
  override: "手動変更",
  one_off: "臨時",
};

const BYDAY: Record<string, string> = { MO: "月", TU: "火", WE: "水", TH: "木", FR: "金", SA: "土", SU: "日" };

/** よく使う RRULE を日本語で説明する（対応しない形は RRULE をそのまま返す） */
export function describeRrule(rrule: string): string {
  const parts = Object.fromEntries(rrule.split(";").map((p) => p.split("=") as [string, string]));
  const interval = parts.INTERVAL && parts.INTERVAL !== "1" ? parts.INTERVAL : "";
  switch (parts.FREQ) {
    case "DAILY":
      return interval ? `${interval}日ごと` : "毎日";
    case "WEEKLY": {
      if (!parts.BYDAY) return interval ? `${interval}週ごと` : "毎週";
      const days = parts.BYDAY.split(",");
      const label =
        days.length === 5 && ["MO", "TU", "WE", "TH", "FR"].every((d) => days.includes(d))
          ? "平日"
          : days.map((d) => BYDAY[d] ?? d).join("・");
      return `${interval ? `${interval}週ごと` : "毎週"} ${label}`;
    }
    case "MONTHLY":
      if (parts.BYMONTHDAY) {
        const days = parts.BYMONTHDAY.split(",").map((d) => (d === "-1" ? "末日" : `${d}日`));
        return `${interval ? `${interval}か月ごと` : "毎月"}${days.join("・")}`;
      }
      return rrule;
    default:
      return rrule;
  }
}

/** テンプレートの生成ルールを日本語で説明する */
export function describeTemplate(t: CalendarTemplateInfo): string {
  if (t.template_type === "rrule" && t.rrule) return describeRrule(t.rrule);
  if (t.template_type === "explicit_dates") return "顧客指定日";
  if (t.template_type === "reference_sheet_based") {
    const marker = `シート「${t.reference_event_marker ?? "?"}」`;
    const n = t.offset_days ?? 0;
    if (n === 0) return `${marker}の当日`;
    const unit = t.offset_day_unit === "business" ? "営業日" : "日";
    return `${marker}の${Math.abs(n)}${unit}${n < 0 ? "前" : "後"}`;
  }
  return t.template_type;
}
