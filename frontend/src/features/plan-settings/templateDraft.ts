// テンプレートの下書き（Level 2）とフォームの値の変換、「今 → 下書き」の要約。UI から独立した純粋な処理。
import { CATEGORY_LABEL, categoryOf } from "../../domain/plan/lib/category";
import { describeDeadline, minutesLabel } from "../../domain/plan/lib/deadline";
import { describeRrule, describeTemplate, POLICY_LABEL, TEMPLATE_TYPE_LABEL } from "../../domain/plan/lib/describe";
import type {
  CreatePlanTemplateInput,
  DayUnit,
  HolidayPolicy,
  PlanTemplate,
  TemplateChange,
  TemplateDraftInput,
  UIMetadataValue,
  UpdatePlanTemplateInput,
} from "../../domain/plan/types";
import { diffDates, fromTemplate, toCreateInput, toUpdateInput, type FormValues } from "./templateForm";

type Changes = Partial<UpdatePlanTemplateInput>;
type ChangeKey = keyof UpdatePlanTemplateInput;

// ---------------------------------------------------------------------------
// フォーム → 下書きの本文
// ---------------------------------------------------------------------------

/**
 * 比べるための形（null と未指定は同じ、表示設定は値の無いキーを除いてキー順をそろえる）。
 * 表示設定の分類が無い（分類を導入する前のテンプレート・ui_metadata が null）は定例として比べる。
 * フォームは常に分類を送るため、そうしないと何も変えていないフォームが「表示の変更」の下書きになる（plan_check で 19 件中 10 件）。
 */
function comparable(key: string, value: unknown): string {
  if (key === "ui_metadata") {
    const ui = (value ?? {}) as Record<string, unknown>;
    const entries = Object.entries({ ...ui, category: categoryOf(ui as UIMetadataValue) })
      .filter(([, v]) => v !== null && v !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify(entries);
  }
  if (value === undefined || value === null) return "null";
  return JSON.stringify(value);
}

/**
 * 更新の入力のうち、今のテンプレートと値が違う項目だけを残す。
 * 下書きには「何を変えるか」だけを残す（承認の画面の「今 → 下書き」も、この項目だけになる）。
 */
export function changedFields(input: UpdatePlanTemplateInput, t: PlanTemplate): Changes {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    if (comparable(key, value) !== comparable(key, (t as Record<string, unknown>)[key])) result[key] = value;
  }
  return result as Changes;
}

/**
 * 編集フォームの値から、変更の下書きの本文を作る（項目の変更と指定日の追加・削除を 1 つの下書きに）。
 * 何も変わらなければ null（「変更がありません」と伝える。バックエンドも 400 で断る）。
 */
export function updateDraftBody(v: FormValues, t: PlanTemplate): TemplateDraftInput | null {
  const changes = changedFields(toUpdateInput(v, t), t);
  const { added, removed } = t.template_type === "explicit_dates" ? diffDates(v, t) : { added: [], removed: [] };
  if (Object.keys(changes).length === 0 && added.length === 0 && removed.length === 0) return null;
  return { kind: "update", template_id: t.id, changes, add_dates: added, remove_dates: removed };
}

/** 新規作成フォームの値から、新規作成の下書きの本文を作る（直接の作成と同じ本文） */
export function createDraftBody(v: FormValues, bizId: number): TemplateDraftInput {
  return { kind: "create", template: toCreateInput(v, bizId) };
}

export function deleteDraftBody(t: Pick<PlanTemplate, "id">): TemplateDraftInput {
  return { kind: "delete", template_id: t.id };
}

// ---------------------------------------------------------------------------
// 下書き → フォームの値（「直す」で開くとき）
// ---------------------------------------------------------------------------

/** 種類によって使わない項目の既定値（新規作成の本文には含まれないことがある） */
const EMPTY_TEMPLATE_FIELDS = {
  anchor_date: null,
  rrule: null,
  reference_sheet_id: null,
  reference_event_marker: null,
  offset_days: null,
  offset_day_unit: null,
  offset_minutes: null,
  business_calendar_id: null,
  ui_metadata: null,
  deadline_offset_minutes: null,
  deadline_time: null,
  deadline_day_offset: null,
};

const asDates = (dates: string[]) => [...new Set(dates)].sort().map((local_date) => ({ local_date }));

/** 変更の下書きを当てはめた後のテンプレート（今のテンプレート + 変える項目 + 指定日の追加・削除） */
export function proposedTemplate(change: TemplateChange, t: PlanTemplate): PlanTemplate {
  const changes = (change.changes ?? {}) as Changes;
  const removed = new Set(change.remove_dates);
  const dates = t.explicit_dates.map((d) => d.local_date).filter((d) => !removed.has(d)).concat(change.add_dates);
  return { ...t, ...changes, ui_metadata: "ui_metadata" in changes ? (changes.ui_metadata as UIMetadataValue) : t.ui_metadata, explicit_dates: asDates(dates) } as PlanTemplate;
}

/** 新規作成の下書きの本文を、テンプレートの形に（フォームの初期値・要約用。id などは仮の値） */
export function templateOfCreateDraft(change: TemplateChange): PlanTemplate {
  const body = (change.changes ?? {}) as CreatePlanTemplateInput & { dates?: string[] };
  return {
    ...EMPTY_TEMPLATE_FIELDS,
    ...body,
    id: 0,
    revision: 0,
    materialized_until: null,
    next_occurrence_at: null,
    pending_change_id: null,
    explicit_dates: asDates(body.dates ?? []),
  } as unknown as PlanTemplate;
}

/** 「直す」で開くフォームの値（下書きの中身。変更なら今のテンプレートに当てはめた姿） */
export function formValuesOfDraft(change: TemplateChange, t: PlanTemplate | null): FormValues {
  if (change.kind === "create") return fromTemplate(templateOfCreateDraft(change));
  if (!t) throw new Error("the template of an update draft is needed");
  return fromTemplate(proposedTemplate(change, t));
}

// ---------------------------------------------------------------------------
// 要約: 今 → 下書き
// ---------------------------------------------------------------------------

export interface SummaryLine {
  label: string;
  before: string;
  after: string;
}

/** 表示の名前（シート・カレンダーは ID しか持たないので、画面から名前の引き方を渡せる） */
export interface SummaryNames {
  sheet?: (id: number) => string | undefined;
  calendar?: (id: number) => string | undefined;
}

const NONE = "（なし）";
const DEADLINE_KEYS: ChangeKey[] = ["deadline_mode", "deadline_offset_minutes", "deadline_time", "deadline_day_offset", "warn_before_minutes"];
const DAY_UNIT_LABEL: Record<DayUnit, string> = { calendar: "暦日", business: "営業日" };

/** 項目の表示順とラベル（締切の項目はまとめて 1 行） */
const FIELD_LABEL: [ChangeKey, string][] = [
  ["name", "予定名"],
  ["rrule", "繰り返し"],
  ["anchor_date", "起点日"],
  ["reference_sheet_id", "年間計画シート"],
  ["reference_event_marker", "イベント記号"],
  ["offset_days", "基準日からのずれ"],
  ["offset_day_unit", "ずれの数え方"],
  ["offset_minutes", "時刻のずれ"],
  ["start_time", "開始時刻"],
  ["duration_minutes", "所要時間"],
  ["timezone", "タイムゾーン"],
  ["holiday_policy", "休日の扱い"],
  ["business_calendar_id", "営業日カレンダー"],
  ["ui_metadata", "表示"],
];

function describeUi(ui: UIMetadataValue | null | undefined): string {
  const parts = [`分類: ${CATEGORY_LABEL[categoryOf(ui ?? undefined)]}`];
  if (typeof ui?.bg_color === "string") parts.push(`色 ${ui.bg_color}`);
  if (typeof ui?.icon === "string") parts.push(`アイコン ${ui.icon}`);
  if (ui?.is_bold === true) parts.push("太字");
  return parts.join("・");
}

function formatValue(key: ChangeKey, value: unknown, names: SummaryNames): string {
  if (key === "ui_metadata") return describeUi(value as UIMetadataValue | null);
  if (value === null || value === undefined || value === "") return NONE;
  switch (key) {
    case "start_time":
      return String(value).slice(0, 5);
    case "duration_minutes":
      return minutesLabel(Number(value));
    case "offset_minutes": {
      const n = Number(value);
      return n === 0 ? "なし" : `${minutesLabel(Math.abs(n))}${n < 0 ? "前" : "後"}`;
    }
    case "offset_days": {
      const n = Number(value);
      return n === 0 ? "当日" : `${Math.abs(n)}日${n < 0 ? "前" : "後"}`;
    }
    case "offset_day_unit":
      return DAY_UNIT_LABEL[value as DayUnit] ?? String(value);
    case "holiday_policy":
      return POLICY_LABEL[value as HolidayPolicy] ?? String(value);
    case "rrule":
      return `${describeRrule(String(value))}（${value}）`;
    case "reference_sheet_id":
      return names.sheet?.(Number(value)) ?? `シート #${value}`;
    case "business_calendar_id":
      return names.calendar?.(Number(value)) ?? `カレンダー #${value}`;
    default:
      return String(value);
  }
}

const datesText = (dates: string[]) => (dates.length ? dates.join("、") : NONE);

/**
 * 下書きの要約（「今 → 下書き」の行）。承認の画面・下書きを見る窓で使う。
 * - 変更: 変える項目だけ（締切はまとめて 1 行）と、指定日の追加・削除
 * - 新規作成: 作るテンプレートの主な項目（「今」は —）
 * - 削除: 1 行
 * t: 変更・削除の対象のテンプレート（今の値）。新規作成では不要。
 */
export function changeSummary(change: TemplateChange, t: PlanTemplate | null, names: SummaryNames = {}): SummaryLine[] {
  if (change.kind === "delete") {
    return [{ label: "テンプレート", before: t?.name ?? change.template_name ?? NONE, after: "削除する（未来の予定は無くなります）" }];
  }
  if (change.kind === "create") {
    const p = templateOfCreateDraft(change);
    const lines: SummaryLine[] = [
      { label: "予定名", before: "—", after: p.name },
      { label: "種類", before: "—", after: TEMPLATE_TYPE_LABEL[p.template_type] },
      { label: "ルール", before: "—", after: describeTemplate(p) },
      { label: "開始時刻", before: "—", after: formatValue("start_time", p.start_time, names) },
      { label: "所要時間", before: "—", after: formatValue("duration_minutes", p.duration_minutes, names) },
      { label: "休日の扱い", before: "—", after: formatValue("holiday_policy", p.holiday_policy, names) },
      { label: "締切", before: "—", after: describeDeadline(p) },
    ];
    if (p.template_type === "explicit_dates") {
      lines.push({ label: "指定日", before: "—", after: datesText(p.explicit_dates.map((d) => d.local_date)) });
    }
    return lines;
  }

  if (!t) throw new Error("the template of an update draft is needed");
  const changes = (change.changes ?? {}) as Record<string, unknown>;
  const lines: SummaryLine[] = [];
  for (const [key, label] of FIELD_LABEL) {
    if (key in changes) lines.push({ label, before: formatValue(key, t[key as keyof PlanTemplate], names), after: formatValue(key, changes[key], names) });
  }
  if (DEADLINE_KEYS.some((k) => k in changes)) {
    lines.push({ label: "締切", before: describeDeadline(t), after: describeDeadline(proposedTemplate(change, t)) });
  }
  if (change.add_dates.length) lines.push({ label: "指定日を追加", before: "—", after: datesText(change.add_dates) });
  if (change.remove_dates.length) lines.push({ label: "指定日を外す", before: datesText(change.remove_dates), after: "—" });
  return lines;
}
