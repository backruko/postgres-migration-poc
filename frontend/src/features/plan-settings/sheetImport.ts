// 年間計画シートの取り込み（JSON ファイル）の読み取りと、取り込む前の確認。UI から独立した純粋な処理。
// ファイルの中身の検証はバックエンドが行う（問題をすべて列挙して返す）。ここでは、形が違って送れないものだけを先に止める。
import type { ReferenceSheet, SheetImportInput } from "../../domain/plan/types";

export type ParseResult = { ok: true; input: SheetImportInput } | { ok: false; problems: string[] };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isDate = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

/**
 * ファイルの文字列を取り込みの入力にする。JSON として読めない・必要な項目が無い・型が違う場合は、理由をすべて返す。
 * 余分な項目は送らない（バックエンドの入力に合わせて必要な項目だけを取り出す）。
 */
export function parseImportFile(text: string): ParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text.replace(/^﻿/, "")); // Excel などが付ける BOM は取り除く
  } catch {
    return { ok: false, problems: ["JSON として読めません（ファイルの形式を確認してください）。"] };
  }
  if (!isRecord(raw)) return { ok: false, problems: ["JSON のいちばん外側はオブジェクト（{ … }）にしてください。"] };

  const problems: string[] = [];
  for (const key of ["client_code", "sheet_name"] as const) {
    if (typeof raw[key] !== "string" || !raw[key].trim()) problems.push(`${key} がありません（文字列）。`);
  }
  for (const key of ["period_start", "period_end"] as const) {
    if (!isDate(raw[key])) problems.push(`${key} がありません（YYYY-MM-DD）。`);
  }

  const markers: SheetImportInput["markers"] = [];
  if (!Array.isArray(raw.markers) || raw.markers.length === 0) {
    problems.push("markers がありません（1 件以上の配列）。");
  } else {
    raw.markers.forEach((m: unknown, i: number) => {
      if (!isRecord(m) || typeof m.marker !== "string" || !m.marker.trim()) {
        problems.push(`markers[${i}]: marker がありません（文字列）。`);
      } else if (m.description != null && typeof m.description !== "string") {
        problems.push(`markers[${i}]: description は文字列にしてください。`);
      } else {
        markers.push({ marker: m.marker.trim(), description: (m.description as string | null | undefined)?.trim() || null });
      }
    });
  }

  const events: NonNullable<SheetImportInput["events"]> = [];
  if (raw.events !== undefined && !Array.isArray(raw.events)) {
    problems.push("events は配列にしてください。");
  } else {
    const bad: number[] = [];
    (raw.events ?? []).forEach((e: unknown, i: number) => {
      if (isRecord(e) && isDate(e.event_date) && typeof e.marker === "string" && e.marker.trim()) {
        events.push({ event_date: e.event_date, marker: e.marker.trim() });
      } else {
        bad.push(i);
      }
    });
    if (bad.length > 0) {
      problems.push(
        `events の ${bad.length} 件に event_date（YYYY-MM-DD）か marker がありません（例: ${bad.slice(0, 5).map((i) => `events[${i}]`).join("、")}）。`,
      );
    }
  }

  if (problems.length > 0) return { ok: false, problems };
  return {
    ok: true,
    input: {
      client_code: (raw.client_code as string).trim(),
      sheet_name: (raw.sheet_name as string).trim(),
      period_start: raw.period_start as string,
      period_end: raw.period_end as string,
      markers,
      events,
    },
  };
}

export interface ImportPreview {
  /** 同じ名前のシートが既にあれば、そのシート（更新になる）。無ければ null（新規作成） */
  existing: ReferenceSheet | null;
  /** 実際に送る内容（「追加だけ」では、期間を今のシートとファイルの両方を含む範囲に広げる） */
  effective: SheetImportInput;
  /** 期間をファイルより広げたか */
  periodWidened: boolean;
  /** このままでは取り込めない理由（バックエンドも拒否する・または顧客の取り違え） */
  blockers: string[];
  /** 新しく作られるマーカー */
  newMarkers: string[];
  /** 追加される日付の数 */
  toAdd: number;
  /** 削除される日付の数（「ファイルに合わせる」のときだけ） */
  toDelete: number;
  /** 「ファイルに合わせる」で日付がすべて消えるマーカー（ファイルに日付が 1 件も無いもの）と、消える件数 */
  emptied: { marker: string; count: number }[];
  /** ファイルに無くても残る、人が変更・追加・取消した日付の数（「ファイルに合わせる」のときだけ数える） */
  keptManual: number;
}

/**
 * 取り込むと何が変わるかを、今のシートと比べて数える（バックエンドの import_sheet と同じ決まり）:
 * - 日付は、無いものを追加する。人が動かした・取り消した日付の元の日付は、ファイルにあっても追加し直さない
 * - 「ファイルに合わせる」では、ファイルに無い日付を削除する。人が変更・追加・取消した日付は削除しない
 * - ルールで日付を作るマーカーは取り込みの対象外（ファイルに日付があると拒否される）
 *
 * 期間: バックエンドはシートの期間をファイルの期間で置き換え、「追加だけ」では今ある日付が期間の外に出ると拒否する。
 * 顧客の計画は年ごとに届く（2026 年分、次に 2027 年分）ので、「追加だけ」のときは期間を今のシートとファイルの両方を含む範囲に
 * 広げて送る（新しい年のファイルをそのまま足せる）。「ファイルに合わせる」ではファイルの期間をそのまま使う。
 */
export function previewImport(
  input: SheetImportInput,
  sheets: ReferenceSheet[],
  selectedClient: string,
  replaceEvents: boolean,
): ImportPreview {
  const blockers: string[] = [];
  if (input.client_code.toUpperCase() !== selectedClient.toUpperCase()) {
    blockers.push(
      `ファイルの顧客コード（${input.client_code}）が、選択中の顧客（${selectedClient}）と違います。顧客を選び直すか、ファイルを確認してください。`,
    );
  }

  const existing = sheets.find((s) => s.name === input.sheet_name) ?? null;
  // 日付は YYYY-MM-DD なので、文字列の比較で前後が決まる
  const effective: SheetImportInput =
    existing && !replaceEvents
      ? {
          ...input,
          period_start: input.period_start < existing.period_start ? input.period_start : existing.period_start,
          period_end: input.period_end > existing.period_end ? input.period_end : existing.period_end,
        }
      : input;
  const periodWidened = effective.period_start !== input.period_start || effective.period_end !== input.period_end;
  const wanted = new Map<string, Set<string>>();
  for (const e of input.events ?? []) {
    if (!wanted.has(e.marker)) wanted.set(e.marker, new Set());
    wanted.get(e.marker)!.add(e.event_date);
  }
  const current = new Map((existing?.markers ?? []).map((m) => [m.marker, m]));

  const generated = [...wanted.keys()].filter((name) => current.get(name)?.is_generated).sort();
  if (generated.length > 0) {
    blockers.push(
      `マーカー ${generated.map((n) => `「${n}」`).join("、")} はルールで日付を作っているため、取り込めません。ファイルから外すか、画面で日付を 1 件ずつ変更してください。`,
    );
  }

  const newMarkers = input.markers.map((m) => m.marker).filter((name) => !current.has(name));
  let toAdd = 0;
  let toDelete = 0;
  let keptManual = 0;
  const emptied: ImportPreview["emptied"] = [];

  // 新しいマーカーの日付はすべて追加
  for (const name of newMarkers) toAdd += wanted.get(name)?.size ?? 0;

  for (const [name, m] of current) {
    if (m.is_generated) continue;
    const dates = wanted.get(name);
    if (!dates && !replaceEvents) continue;
    const have = new Set(m.events.map((e) => e.event_date));
    const decided = new Set(m.events.filter((e) => e.is_manual && e.original_date).map((e) => e.original_date as string));
    for (const d of dates ?? []) if (!have.has(d) && !decided.has(d)) toAdd += 1;
    if (replaceEvents) {
      const missing = m.events.filter((e) => !dates?.has(e.event_date));
      const removable = missing.filter((e) => !e.is_manual).length;
      toDelete += removable;
      keptManual += missing.length - removable;
      if (!dates && removable > 0) emptied.push({ marker: name, count: removable });
    }
  }

  return { existing, effective, periodWidened, blockers, newMarkers, toAdd, toDelete, emptied, keptManual };
}
