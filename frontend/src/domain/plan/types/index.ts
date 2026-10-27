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

// ---- インスタンスの手動操作 ----
export type PlanInstance = Schemas["PlanInstanceResponse"];
export type OverrideInstanceInput = Schemas["PlanInstanceOverride"];
export type CancelInstanceInput = Schemas["PlanInstanceCancel"];
export type RevertInstanceInput = Schemas["PlanInstanceRevert"];
export type RevertResult = Schemas["PlanRevertResponse"];
export type OneOffInstanceInput = Schemas["PlanOneOffCreate"];
export type CancelledInstance = Schemas["CancelledInstanceResponse"];
