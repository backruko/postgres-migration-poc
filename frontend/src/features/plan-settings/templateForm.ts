// 予定テンプレートの作成・編集フォームの値と、API の入力（OpenAPI の型）との変換。UI から独立した純粋な処理。
import { categoryOf, DEFAULT_CATEGORY, type TemplateCategory } from "../../domain/plan/lib/category";
import type {
  CreatePlanTemplateInput,
  DayUnit,
  HolidayPolicy,
  PlanTemplate,
  TemplateType,
  UIMetadataInput,
  UIMetadataValue,
  UpdatePlanTemplateInput,
} from "../../domain/plan/types";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn, type Dayjs } from "../../shared/lib/datetime";

/** フォームの入力値（送信時に template_type ごとの API の形へ変換する） */
export interface FormValues {
  template_type: TemplateType;
  name: string;
  category: TemplateCategory;
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

/** 新規作成の初期値 */
export function initialValues(): Partial<FormValues> {
  return {
    template_type: "rrule",
    category: DEFAULT_CATEGORY,
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

/** 既存テンプレートをフォームの値に（編集の初期値）。種類に関係しない項目は null のため undefined にする */
export function fromTemplate(t: PlanTemplate): FormValues {
  const ui = t.ui_metadata ?? {};
  return {
    template_type: t.template_type,
    name: t.name,
    category: categoryOf(t.ui_metadata),
    start_time: dayjs(t.start_time.slice(0, 5), "HH:mm"),
    duration_minutes: t.duration_minutes,
    timezone: t.timezone,
    holiday_policy: t.holiday_policy,
    business_calendar_id: t.business_calendar_id ?? undefined,
    anchor_date: t.anchor_date ? dayjs(t.anchor_date, "YYYY-MM-DD") : undefined,
    rrule: t.rrule ?? undefined,
    dates: t.explicit_dates.map((d) => dayjs(d.local_date, "YYYY-MM-DD")),
    reference_sheet_id: t.reference_sheet_id ?? undefined,
    reference_event_marker: t.reference_event_marker ?? undefined,
    offset_days: t.offset_days ?? 0,
    offset_day_unit: t.offset_day_unit ?? "calendar",
    offset_minutes: t.offset_minutes ?? 0,
    bg_color: typeof ui.bg_color === "string" ? ui.bg_color : undefined,
    icon: typeof ui.icon === "string" ? ui.icon : undefined,
    is_bold: ui.is_bold === true,
  };
}

/**
 * フォームの分類・表示設定を ui_metadata に（分類は常に保存する）。
 * ui_metadata は更新時に丸ごと置き換わるため、フォームで扱わないキー（text_color など）は元の値から引き継ぐ。
 */
function toUiMetadata(v: FormValues, current: UIMetadataValue | undefined): UIMetadataInput {
  const { category: _category, bg_color: _bg, icon: _icon, is_bold: _bold, ...rest } = current ?? {};
  return {
    ...rest,
    category: v.category,
    ...(v.bg_color ? { bg_color: v.bg_color } : {}),
    ...(v.icon ? { icon: v.icon } : {}),
    ...(v.is_bold ? { is_bold: true } : {}),
  };
}

/** 種類に関係なく共通の項目 */
function commonFields(v: FormValues) {
  return {
    name: v.name.trim(),
    timezone: v.timezone.trim(),
    start_time: v.start_time.format("HH:mm:00"),
    duration_minutes: v.duration_minutes,
    business_calendar_id: v.business_calendar_id ?? null,
    holiday_policy: v.holiday_policy,
  };
}

const rruleFields = (v: FormValues) => ({ anchor_date: v.anchor_date!.format("YYYY-MM-DD"), rrule: v.rrule!.trim() });

const sheetFields = (v: FormValues) => ({
  reference_sheet_id: v.reference_sheet_id!,
  reference_event_marker: v.reference_event_marker!,
  offset_days: v.offset_days ?? 0,
  offset_day_unit: v.offset_day_unit ?? "calendar",
  offset_minutes: v.offset_minutes ?? 0,
});

const formatDates = (dates: Dayjs[] | undefined) => (dates ?? []).map((d) => d.format("YYYY-MM-DD"));

/** フォームの値を、OpenAPI の判別共用体（RRuleTemplateCreate など）の形に変換する */
export function toCreateInput(v: FormValues, bizId: number): CreatePlanTemplateInput {
  const base = { biz_master_id: bizId, ...commonFields(v), ui_metadata: toUiMetadata(v, null) };
  switch (v.template_type) {
    case "rrule":
      return { ...base, template_type: "rrule", ...rruleFields(v) };
    case "explicit_dates":
      return { ...base, template_type: "explicit_dates", dates: formatDates(v.dates) };
    case "reference_sheet_based":
      return { ...base, template_type: "reference_sheet_based", ...sheetFields(v) };
  }
}

/**
 * フォームの値を更新の入力（PATCH）に変換する。フォーム全体を送るが、種類に合わない項目は含めない（含めると 400）。
 * 値が変わっていない項目はバックエンドで無視されるため、改訂番号は実際に生成条件が変わったときだけ上がる。
 * 指定日は PATCH では変更できないため、diffDates で差分を取り、別の API で追加・削除する。
 */
export function toUpdateInput(v: FormValues, current: PlanTemplate): UpdatePlanTemplateInput {
  const base = { ...commonFields(v), ui_metadata: toUiMetadata(v, current.ui_metadata) };
  switch (current.template_type) {
    case "rrule":
      return { ...base, ...rruleFields(v) };
    case "explicit_dates":
      return base;
    case "reference_sheet_based":
      return { ...base, ...sheetFields(v) };
  }
}

/** 指定日の差分（YYYY-MM-DD、昇順） */
export function diffDates(v: FormValues, current: PlanTemplate): { added: string[]; removed: string[] } {
  const before = new Set(current.explicit_dates.map((d) => d.local_date));
  const after = new Set(formatDates(v.dates));
  return {
    added: [...after].filter((d) => !before.has(d)).sort(),
    removed: [...before].filter((d) => !after.has(d)).sort(),
  };
}
