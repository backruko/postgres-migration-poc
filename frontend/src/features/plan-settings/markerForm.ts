// 生成マーカー（ルールから基準日を作るマーカー）のフォームの値と、API の入力との変換。UI から独立した純粋な処理。
import type {
  CreateMarkerInput,
  HolidayPolicy,
  MarkerRule,
  ReferenceSheet,
  ReferenceSheetMarker,
  UpdateMarkerInput,
} from "../../domain/plan/types";
import { dayjs, type Dayjs } from "../../shared/lib/datetime";

export interface MarkerFormValues {
  marker: string;
  description?: string;
  // ルール（生成マーカーのみ。取り込んだマーカーは説明だけを編集する）
  rrule?: string;
  anchor_date?: Dayjs;
  holiday_policy?: HolidayPolicy;
  business_calendar_id?: number;
}

export const MARKER_RRULE_PRESETS = [
  "FREQ=MONTHLY;BYMONTHDAY=27",
  "FREQ=MONTHLY;BYMONTHDAY=25",
  "FREQ=MONTHLY;BYMONTHDAY=10",
  "FREQ=MONTHLY;BYMONTHDAY=-1",
];

/** 新規作成の初期値（毎月27日・休日は翌営業日。ルールの起点はシートの開始日） */
export function initialMarkerValues(sheet: ReferenceSheet): Partial<MarkerFormValues> {
  return {
    rrule: "FREQ=MONTHLY;BYMONTHDAY=27",
    anchor_date: dayjs(sheet.period_start, "YYYY-MM-DD"),
    holiday_policy: "following",
  };
}

/** 既存マーカーをフォームの値に（取り込んだマーカーはルール項目が null のため undefined にする） */
export function fromMarker(m: ReferenceSheetMarker): MarkerFormValues {
  return {
    marker: m.marker,
    description: m.description ?? undefined,
    rrule: m.rrule ?? undefined,
    anchor_date: m.anchor_date ? dayjs(m.anchor_date, "YYYY-MM-DD") : undefined,
    holiday_policy: m.holiday_policy,
    business_calendar_id: m.business_calendar_id ?? undefined,
  };
}

const toDescription = (v: MarkerFormValues) => v.description?.trim() || null;

function toRule(v: MarkerFormValues): MarkerRule {
  return {
    rrule: v.rrule!.trim(),
    anchor_date: v.anchor_date!.format("YYYY-MM-DD"),
    holiday_policy: v.holiday_policy ?? "as_is",
    business_calendar_id: v.business_calendar_id ?? null,
  };
}

/** フォームのルールが保存済みのルールと違うか（生成マーカーの編集で、警告を出すかの判定に使う） */
export function ruleChanged(v: Partial<MarkerFormValues>, m: ReferenceSheetMarker): boolean {
  if (!v.rrule || !v.anchor_date) return false;
  const r = toRule(v as MarkerFormValues);
  return (
    r.rrule !== m.rrule ||
    r.anchor_date !== m.anchor_date ||
    r.holiday_policy !== m.holiday_policy ||
    (r.business_calendar_id ?? null) !== m.business_calendar_id
  );
}

export function toCreateMarkerInput(v: MarkerFormValues): CreateMarkerInput {
  return { marker: v.marker.trim(), description: toDescription(v), rule: toRule(v) };
}

/**
 * 更新の入力（PATCH）。生成マーカーは説明とルールを送る（ルールが同じなら日付は変わらず、テンプレートも作り直されない）。
 * 取り込んだマーカーは説明だけを送る（ルールを付けると取り込んだ日付と生成した日付が混ざるため、ここでは扱わない）。
 */
export function toUpdateMarkerInput(v: MarkerFormValues, current: ReferenceSheetMarker): UpdateMarkerInput {
  return current.is_generated ? { description: toDescription(v), rule: toRule(v) } : { description: toDescription(v) };
}

/** 取消されていない日付（昇順） */
export const activeDates = (m: ReferenceSheetMarker) =>
  m.events
    .filter((e) => !e.is_cancelled)
    .map((e) => e.event_date)
    .sort();

/** 今日以降の直近の日付 */
export const upcomingDates = (m: ReferenceSheetMarker, today: string, count: number) =>
  activeDates(m)
    .filter((d) => d >= today)
    .slice(0, count);

/**
 * 人が編集・追加した今日以降の日付（昇順）。
 * これらはルールを変えても残る（人の判断が優先、出どころは is_manual / rule_date で区別される）ため、
 * 新しいルールの日付と同じ時期に重なり、参照するジョブが二重に動くことがある。過去の日付は実績として残してよい。
 */
export const futureManualDates = (m: ReferenceSheetMarker, today: string) =>
  m.events
    .filter((e) => e.is_manual && !e.is_cancelled && e.event_date >= today)
    .map((e) => e.event_date)
    .sort();

/** 人が編集・追加した日付の数と、人が取り消した日付の数（ルールを変えても維持される） */
export function manualCounts(m: ReferenceSheetMarker) {
  return {
    edited: m.events.filter((e) => e.is_manual && !e.is_cancelled).length,
    cancelled: m.events.filter((e) => e.is_cancelled).length,
  };
}
