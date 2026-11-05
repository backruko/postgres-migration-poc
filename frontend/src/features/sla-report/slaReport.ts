// SLA の集計画面の純粋な処理（表の行・月ごとの合計・CSV）。UI から独立している。
import type { SlaRow } from "../../domain/ops/types";

/** 表の1行: 顧客の行か、その月の合計の行（顧客が2つ以上ある月だけ） */
export type SlaTableRow = SlaRow & { key: string; isTotal: boolean };

/** 期限内率（%, 小数1桁）。完了が無ければ null */
export const onTimeRate = (finished: number, late: number) =>
  finished ? Math.round(((finished - late) * 1000) / finished) / 10 : null;

/** 月の合計（平均遅れは、各顧客の平均を遅れた件数で重み付けした全体の平均） */
export function monthTotal(month: string, rows: SlaRow[]): SlaRow {
  const finished = rows.reduce((n, r) => n + r.finished, 0);
  const late = rows.reduce((n, r) => n + r.late, 0);
  const lateMinutes = rows.reduce((n, r) => n + (r.avg_minutes_late ?? 0) * r.late, 0);
  return {
    month,
    client_code: "",
    client_name: "合計",
    finished,
    late,
    on_time_rate: onTimeRate(finished, late),
    avg_minutes_late: late ? Math.round((lateMinutes * 10) / late) / 10 : null,
    open_late: rows.reduce((n, r) => n + r.open_late, 0),
  };
}

/** 表の行: 月の順に顧客の行を並べ、顧客が2つ以上ある月はその後に合計の行を足す */
export function tableRows(rows: SlaRow[]): SlaTableRow[] {
  const byMonth = new Map<string, SlaRow[]>();
  for (const r of rows) byMonth.set(r.month, [...(byMonth.get(r.month) ?? []), r]);
  const out: SlaTableRow[] = [];
  for (const [month, monthRows] of byMonth) {
    out.push(...monthRows.map((r) => ({ ...r, key: `${month}-${r.client_code}`, isTotal: false })));
    if (monthRows.length > 1) out.push({ ...monthTotal(month, monthRows), key: `${month}-total`, isTotal: true });
  }
  return out;
}

export const CSV_HEADER = ["月", "顧客コード", "顧客名", "完了", "遅れ", "期限内率(%)", "平均遅れ(分)", "未完了の遅れ"];

const cell = (v: string | number | null) => {
  const s = v === null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** 表示している行（合計の行を含む）の CSV。Excel で文字化けしないよう、先頭に BOM を付ける */
export function toCsv(rows: SlaTableRow[]): string {
  const lines = [
    CSV_HEADER,
    ...rows.map((r) => [
      r.month, r.isTotal ? "" : r.client_code, r.client_name, r.finished, r.late, r.on_time_rate, r.avg_minutes_late, r.open_late,
    ]),
  ];
  return "﻿" + lines.map((l) => l.map(cell).join(",")).join("\r\n") + "\r\n";
}

/** CSV のファイル名（例: sla_2025-12_2026-11.csv / sla_2025-12_2026-11_MOCK.csv） */
export const csvFileName = (from: string, to: string, clientCode: string | null) =>
  `sla_${from}_${to}${clientCode ? `_${clientCode}` : ""}.csv`;
