// plan ドメインの型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名で、手書きの型は持たない。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

// ---- 列挙 ----
export type TemplateType = Schemas["TemplateType"];
export type InstanceOrigin = Schemas["InstanceOrigin"];
export type HolidayPolicy = Schemas["HolidayPolicy"];
export type DayUnit = Schemas["DayUnit"];

// ---- カレンダー表示 ----
export type ClientCalendar = Schemas["ClientCalendarResponse"];
export type CalendarDay = Schemas["CalendarDay"];
export type CalendarTemplateInfo = Schemas["CalendarTemplateInfo"];
export type ClientOccurrence = Schemas["ClientOccurrenceResponse"];
export type BusinessCalendar = Schemas["BusinessCalendarResponse"];

/** カレンダー表示用スタイル（レスポンスの ui_metadata はキー自由の辞書） */
export type UIMetadataInput = Schemas["UIMetadata"];
export type UIMetadataValue = ClientOccurrence["ui_metadata"];

// ---- テンプレート（事前設定） ----
export type PlanTemplate = Schemas["PlanTemplateResponse"];
/** テンプレート作成の入力（template_type で形が変わる判別共用体） */
export type CreatePlanTemplateInput =
  | Schemas["RRuleTemplateCreate"]
  | Schemas["ExplicitDatesTemplateCreate"]
  | Schemas["ReferenceSheetTemplateCreate"];
/** テンプレート更新の入力（部分更新。種類と業務は変更不可、種類に合わない項目は送れない） */
export type UpdatePlanTemplateInput = Schemas["PlanTemplateUpdate"];

// ---- 年間計画シート ----
export type ReferenceSheet = Schemas["ReferenceSheetResponse"];
export type ReferenceSheetMarker = Schemas["ReferenceSheetMarkerResponse"];
export type ReferenceSheetEvent = Schemas["ReferenceSheetEventResponse"];
/** 生成マーカーのルール（例: 毎月27日、休日なら翌営業日） */
export type MarkerRule = Schemas["MarkerRule"];
export type CreateMarkerInput = Schemas["ReferenceSheetMarkerCreate"];
/** rule: 指定で置き換えて再生成、null で生成をやめる、省略で変更しない */
export type UpdateMarkerInput = Schemas["ReferenceSheetMarkerUpdate"];

// ---- インスタンスの手動操作 ----
export type PlanInstance = Schemas["PlanInstanceResponse"];
export type OverrideInstanceInput = Schemas["PlanInstanceOverride"];
export type CancelInstanceInput = Schemas["PlanInstanceCancel"];
export type RevertInstanceInput = Schemas["PlanInstanceRevert"];
export type RevertResult = Schemas["PlanRevertResponse"];
export type OneOffInstanceInput = Schemas["PlanOneOffCreate"];
export type CancelledInstance = Schemas["CancelledInstanceResponse"];
