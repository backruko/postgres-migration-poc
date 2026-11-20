// 「ワーカーの状態」（並行稼働）と日次ダッシュボードの赤い帯の純粋な処理（言葉・帯の文・期間・表の行・CSV）。UI から独立している。
import type { WorkerHealthDay, WorkerHealthState, WorkerHealthTotal } from "../../domain/worker/types";
import { dayjs } from "../../shared/lib/datetime";

/** 状態の言葉と色 */
export const STATE_LABEL: Record<string, string> = {
  ok: "動いている",
  stale: "止まっている（心拍が途絶えた）",
  stopped: "止まっている（停止した）",
  never: "まだ一度も動いていない",
};
export const STATE_COLOR: Record<string, string> = { ok: "green", stale: "red", stopped: "red", never: "red" };

/** 仕事の名前（worker_error.job） */
export const JOB_LABEL: Record<string, string> = {
  release: "自動リリース",
  extension: "予定の生成",
  deadline_watch: "締切の監視",
  dispatch: "通知の振り分け",
  delivery: "通知の配信",
  worker: "ワーカー本体",
};
export const jobLabel = (job: string) => JOB_LABEL[job] ?? job;

/** きれいに止まらなかった理由 */
export const UNCLEAN_LABEL: Record<string, string> = {
  crash: "落ちた（想定外のエラー）",
  no_stop: "止まった印のないまま途絶えた（強制終了・ハング）",
};

/** 日時の短い書き方: 今日なら「HH:mm」、ほかの日なら「M/D HH:mm」 */
export function shortTime(iso: string, tz: string, now: Date = new Date()): string {
  const t = dayjs(iso).tz(tz);
  return t.format("YYYY-MM-DD") === dayjs(now).tz(tz).format("YYYY-MM-DD") ? t.format("HH:mm") : t.format("M/D HH:mm");
}

const STOPPED_WORK = "自動リリース・締切の監視・通知の送信が止まっています。";

/** 日次ダッシュボードの赤い帯の文（動いていれば・まだ読めていなければ null） */
export function workerBannerText(health: WorkerHealthState | null | undefined, tz: string, now: Date = new Date()): string | null {
  if (!health || health.ok) return null;
  if (health.state === "never" || !health.last_beat_at) return `ワーカーがまだ一度も動いていません。${STOPPED_WORK.replace("止まっています", "動いていません")}`;
  const minutes = Math.floor((health.seconds_since_beat ?? 0) / 60);
  const last = shortTime(health.last_beat_at, tz, now);
  if (health.state === "stopped") return `ワーカーが止まっています（最後の動作 ${last}）。${STOPPED_WORK}`;
  return `ワーカーが ${minutes} 分間動いていません（最後の動作 ${last}）。${STOPPED_WORK}`;
}

/** 期間の確かめ（サーバーと同じ: 最大 62 日・始めが終わりより後でない）。よければ null */
export const MAX_DAYS = 62;
export function periodProblem(from: string, to: string): string | null {
  const days = dayjs(to, "YYYY-MM-DD").diff(dayjs(from, "YYYY-MM-DD"), "day") + 1;
  if (days < 1) return "期間の始めが終わりより後になっています。";
  if (days > MAX_DAYS) return `期間は最大 ${MAX_DAYS} 日です（いまは ${days} 日）。`;
  return null;
}

/** 合計の判定の文 */
export function verdictText(total: WorkerHealthTotal): string {
  if (total.ok) return `この ${total.days} 日間に、承認に数える問題はありません。`;
  const parts = [
    total.errors ? `エラー ${total.errors}` : null,
    total.gaps ? `5 分を超える途切れ ${total.gaps}` : null,
    total.unclean_ends ? `きれいに止まらなかった実行 ${total.unclean_ends}` : null,
  ].filter(Boolean);
  return `この ${total.days} 日間に、承認に数える問題が ${total.problems} 件あります（${parts.join("・")}）。`;
}

/** 仕事ごとのエラーの数（「自動リリース 2・通知の振り分け 1」。無ければ空） */
export const errorsByJobText = (byJob: Record<string, number>) =>
  Object.entries(byJob).map(([job, n]) => `${jobLabel(job)} ${n}`).join("・");

/** 1 日の表の行（新しい日が上） */
export const dayRows = (days: WorkerHealthDay[]) => [...days].reverse();

const num = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "" : String(Number(v.toFixed(digits))));

export const CSV_HEADER = [
  "日付", "判定", "問題", "回り", "稼働率(%)", "最長の間隔(分)", "最も遅い回り(ms)", "エラー", "エラーの内訳", "途切れ(5分超)",
  "きれいに止まらなかった", "起動", "届かなかった通知(数えない)", "諦めた出来事(数えない)",
];

const cell = (v: string | number | null | undefined) => {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** 1 日ごとの行と合計の行の CSV（承認の記録用）。Excel で文字化けしないよう、先頭に BOM を付ける */
export function toCsv(days: WorkerHealthDay[], total: WorkerHealthTotal): string {
  const lines: (string | number | null | undefined)[][] = [
    CSV_HEADER,
    ...days.map((d) => [
      d.day, d.ok ? "OK" : "NG", d.problems, d.passes, num(d.uptime_percent, 2), num(d.longest_gap_minutes), d.slowest_pass_ms,
      d.errors, errorsByJobText(d.errors_by_job), d.gaps.length, d.unclean_ends.length, d.restarts, d.failed_notifications,
      d.given_up_events,
    ]),
    [
      "合計", total.ok ? "OK" : "NG", total.problems, total.passes, num(total.uptime_percent, 2), num(total.longest_gap_minutes), "",
      total.errors, errorsByJobText(total.errors_by_job), total.gaps, total.unclean_ends, total.restarts, total.failed_notifications,
      total.given_up_events,
    ],
  ];
  return "﻿" + lines.map((l) => l.map(cell).join(",")).join("\r\n") + "\r\n";
}

/** CSV のファイル名（例: worker_health_2026-11-17_2026-11-30.csv） */
export const csvFileName = (from: string, to: string) => `worker_health_${from}_${to}.csv`;
