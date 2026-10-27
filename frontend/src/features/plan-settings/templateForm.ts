// 予定テンプレート作成フォームの値と、API の入力（OpenAPI の判別共用体）への変換。UI から独立した純粋な処理。
import type { CreatePlanTemplateInput, DayUnit, HolidayPolicy, TemplateType } from "../../domain/plan/types";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn, type Dayjs } from "../../shared/lib/datetime";

/** フォームの入力値（送信時に template_type ごとの API の形へ変換する） */
export interface FormValues {
  template_type: TemplateType;
  name: string;
  start_time: Dayjs;
  duration_minutes: number;
  timezone: string;
  holiday_policy: HolidayPolicy;
  business_calendar_id?: number;
  // rrule
  anchor_date?: Dayjs;
  rrule?: string;
  // explicit_dates
  dates?: Dayjs[];
  // reference_sheet_based
  reference_sheet_id?: number;
  reference_event_marker?: string;
  offset_days?: number;
  offset_day_unit?: DayUnit;
  offset_minutes?: number;
  // ui_metadata
  bg_color?: string;
  icon?: string;
  is_bold?: boolean;
}

export const RRULE_PRESETS = [
  "FREQ=DAILY",
  "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
  "FREQ=WEEKLY;BYDAY=MO,WE,FR",
  "FREQ=MONTHLY;BYMONTHDAY=27",
  "FREQ=MONTHLY;BYMONTHDAY=-1",
];

export function initialValues(): Partial<FormValues> {
  return {
    template_type: "rrule",
    start_time: dayjs("09:00", "HH:mm"),
    duration_minutes: 60,
    timezone: TIMEZONE,
    holiday_policy: "as_is",
    anchor_date: dayjs(todayIn(TIMEZONE)),
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    dates: [],
    offset_days: 0,
    offset_day_unit: "calendar",
    offset_minutes: 0,
    is_bold: false,
  };
}

/** フォームの値を、OpenAPI の判別共用体（RRuleTemplateCreate など）の形に変換する */
export function toCreateInput(v: FormValues, bizId: number): CreatePlanTemplateInput {
  const ui = {
    ...(v.bg_color ? { bg_color: v.bg_color } : {}),
    ...(v.icon ? { icon: v.icon } : {}),
    ...(v.is_bold ? { is_bold: true } : {}),
  };
  const base = {
    biz_master_id: bizId,
    name: v.name.trim(),
    timezone: v.timezone.trim(),
    start_time: v.start_time.format("HH:mm:00"),
    duration_minutes: v.duration_minutes,
    business_calendar_id: v.business_calendar_id ?? null,
    holiday_policy: v.holiday_policy,
    ui_metadata: Object.keys(ui).length ? ui : null,
  };
  switch (v.template_type) {
    case "rrule":
      return { ...base, template_type: "rrule", anchor_date: v.anchor_date!.format("YYYY-MM-DD"), rrule: v.rrule!.trim() };
    case "explicit_dates":
      return { ...base, template_type: "explicit_dates", dates: (v.dates ?? []).map((d) => d.format("YYYY-MM-DD")) };
    case "reference_sheet_based":
      return {
        ...base,
        template_type: "reference_sheet_based",
        reference_sheet_id: v.reference_sheet_id!,
        reference_event_marker: v.reference_event_marker!,
        offset_days: v.offset_days ?? 0,
        offset_day_unit: v.offset_day_unit ?? "calendar",
        offset_minutes: v.offset_minutes ?? 0,
      };
  }
}
