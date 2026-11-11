// plan ドメインの型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名で、手書きの型は持たない。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

// ---- 列挙 ----
export type TemplateType = Schemas["TemplateType"];
export type InstanceOrigin = Schemas["InstanceOrigin"];
export type HolidayPolicy = Schemas["HolidayPolicy"];
export type DayUnit = Schemas["DayUnit"];
export type DeadlineMode = Schemas["DeadlineMode"];

// ---- カレンダー表示 ----
export type ClientCalendar = Schemas["ClientCalendarResponse"];
export type CalendarDay = Schemas["CalendarDay"];
export type CalendarTemplateInfo = Schemas["CalendarTemplateInfo"];
export type ClientOccurrence = Schemas["ClientOccurrenceResponse"];
export type BusinessCalendar = Schemas["BusinessCalendarResponse"];
export type Holiday = Schemas["HolidayResponse"];
export type HolidayInput = Schemas["HolidayIn"];
/** 休日の追加・削除の結果（作り直したテンプレートの数を含む） */
export type CalendarChangeResult = Schemas["CalendarChangeResult"];

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

// ---- 保存の前の確認（何も保存しない試し実行の結果） ----
export type PreviewOccurrence = Schemas["PreviewOccurrence"];
/** テンプレートをこの内容で変えたら何が起きるか（予定の違いと、オーダーへの影響） */
export type TemplateChangePreview = Schemas["TemplateChangePreview"];
/** テンプレートをこの内容で作ったらできる予定 */
export type TemplateCreatePreview = Schemas["TemplateCreatePreview"];

// ---- テンプレートの変更依頼（下書き → 承認 → 反映。Level 2） ----
export type TemplateChangeKind = Schemas["TemplateChangeKind"];
export type TemplateChangeStatus = Schemas["TemplateChangeStatus"];
/** 変更依頼 1 件（職員・業務・テンプレートの名前つき） */
export type TemplateChange = Schemas["PlanTemplateChangeResponse"];
/** 下書きの本文（kind で形が変わる判別共用体） */
export type TemplateDraftInput =
  | Schemas["TemplateCreateDraft"]
  | Schemas["TemplateUpdateDraft"]
  | Schemas["TemplateDeleteDraft"];
export type TemplateUpdateDraftInput = Schemas["TemplateUpdateDraft"];
/** 承認待ちの下書きを、今のデータで当てはめたら何が起きるか（新規作成は create、変更・削除は change） */
export type TemplateDraftPreview = Schemas["TemplateDraftPreview"];

// ---- 年間計画シート ----
export type ReferenceSheet = Schemas["ReferenceSheetResponse"];
export type ReferenceSheetMarker = Schemas["ReferenceSheetMarkerResponse"];
export type ReferenceSheetEvent = Schemas["ReferenceSheetEventResponse"];
/** 生成マーカーのルール（例: 毎月27日、休日なら翌営業日） */
export type MarkerRule = Schemas["MarkerRule"];
export type CreateMarkerInput = Schemas["ReferenceSheetMarkerCreate"];
/** rule: 指定で置き換えて再生成、null で生成をやめる、省略で変更しない */
export type UpdateMarkerInput = Schemas["ReferenceSheetMarkerUpdate"];
/** シートの一括取り込み（JSON ファイルの中身そのまま） */
export type SheetImportInput = Schemas["ReferenceSheetImport"];
export type SheetImportResult = Schemas["ReferenceSheetImportResult"];

// ---- インスタンスの手動操作 ----
export type PlanInstance = Schemas["PlanInstanceResponse"];
export type OverrideInstanceInput = Schemas["PlanInstanceOverride"];
export type CancelInstanceInput = Schemas["PlanInstanceCancel"];
export type RevertInstanceInput = Schemas["PlanInstanceRevert"];
export type RevertResult = Schemas["PlanRevertResponse"];
export type OneOffInstanceInput = Schemas["PlanOneOffCreate"];
export type CancelledInstance = Schemas["CancelledInstanceResponse"];
