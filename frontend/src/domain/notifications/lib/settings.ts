// 通知の設定（宛先・ルール。N.5c）の純粋な処理: 権限・表示の言葉・フォームの値と API の形の変換・日本語のエラー文。
// React に依存しない（jsdom の確認でそのまま呼べる）。
import type { Staff } from "../../staff/types";
import type {
  DestinationInput, LastTest, NotificationDestination, NotificationRule, QuietMode, RuleInput,
} from "../types";

/** 変えられるのは業務管理者（バックエンドの require_roles([ADMIN])） */
export const canEditNotificationSettings = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");

/**
 * その行（顧客なし = 全顧客）を、この職員が変えられるか。担当顧客が限られた業務管理者は、自分の顧客のものだけ
 * （全顧客のものは見えるが変えられない。2026-11-12 の決定）
 */
export function canEditRow(s: Staff | null | undefined, clientCode: string | null): boolean {
  if (!canEditNotificationSettings(s) || !s) return false;
  if (s.client_codes.length === 0) return true;
  return clientCode !== null && s.client_codes.includes(clientCode);
}

/** 限られた職員が、変えられない行に出す説明 */
export const READ_ONLY_FOR_LIMITED = "全顧客の設定です。変えられるのは、全顧客を担当する業務管理者だけです";

export const clientText = (code: string | null) => code ?? "全顧客";

export const QUIET_MODE_LABEL: Record<QuietMode, string> = {
  hold: "終わってから知らせる",
  drop: "知らせない",
};

/** 知らせない時間帯の文（例: 22:00〜07:00 は終わってから知らせる）。なければ null */
export function quietText(r: Pick<NotificationRule, "quiet_start" | "quiet_end" | "quiet_mode">): string | null {
  if (!r.quiet_start || !r.quiet_end || !r.quiet_mode) return null;
  return `${r.quiet_start.slice(0, 5)}〜${r.quiet_end.slice(0, 5)} は${QUIET_MODE_LABEL[r.quiet_mode]}`;
}

export const dedupeText = (minutes: number | null | undefined) => (minutes ? `${minutes} 分は同じオーダー・同じ種類を 1 回` : null);

/** テスト送信の状態の言葉と色 */
export const TEST_STATE: Record<string, { label: string; color: string | undefined }> = {
  waiting: { label: "送信待ち（1 分以内）", color: "processing" },
  sending: { label: "送信中", color: "processing" },
  sent: { label: "届いた", color: "green" },
  failed: { label: "届かなかった", color: "red" },
};

/** まだ結果の出ていないテスト送信があるか（あれば画面が数秒ごとに読み直す） */
export const hasPendingTest = (rows: NotificationDestination[] | null | undefined) =>
  (rows ?? []).some((d) => d.last_test && (d.last_test.state === "waiting" || d.last_test.state === "sending"));

export const testStateOf = (t: LastTest | null | undefined) => (t ? TEST_STATE[t.state] ?? { label: t.state, color: undefined } : null);

// ---------------- フォーム ----------------

/** 宛先のフォームの値 */
export interface DestinationForm {
  name: string;
  address: string;
  client_code: string | null;
  owner: string;
  enabled: boolean;
}

export const destinationToForm = (d: NotificationDestination | null, defaultClient: string | null): DestinationForm =>
  d
    ? { name: d.name, address: d.address, client_code: d.client_code, owner: d.owner ?? "", enabled: d.enabled }
    : { name: "", address: "", client_code: defaultClient, owner: "", enabled: true };

export const formToDestination = (f: DestinationForm): DestinationInput => ({
  name: f.name.trim(),
  address: f.address.trim(),
  client_code: f.client_code || null,
  owner: f.owner.trim() || null,
  enabled: f.enabled,
});

/** ルールのフォームの値（時刻は "HH:mm"） */
export interface RuleForm {
  name: string;
  event_types: string[];
  client_code: string | null;
  biz_master_id: number | null;
  template_code: string;
  destination_ids: number[];
  dedupe_window_minutes: number | null;
  quiet: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  quiet_mode: QuietMode;
  enabled: boolean;
}

export const ruleToForm = (r: NotificationRule | null, defaults: { client: string | null; template: string }): RuleForm =>
  r
    ? {
        name: r.name, event_types: [...r.event_types], client_code: r.client_code, biz_master_id: r.biz_master_id ?? null,
        template_code: r.template_code, destination_ids: r.destinations.map((d) => d.id),
        dedupe_window_minutes: r.dedupe_window_minutes ?? null, quiet: !!r.quiet_start,
        quiet_start: r.quiet_start ? r.quiet_start.slice(0, 5) : null, quiet_end: r.quiet_end ? r.quiet_end.slice(0, 5) : null,
        quiet_mode: r.quiet_mode ?? "hold", enabled: r.enabled,
      }
    : {
        name: "", event_types: ["OrderLate"], client_code: defaults.client, biz_master_id: null, template_code: defaults.template,
        destination_ids: [], dedupe_window_minutes: null, quiet: false, quiet_start: "22:00", quiet_end: "07:00",
        quiet_mode: "hold", enabled: true,
      };

export function formToRule(f: RuleForm): RuleInput {
  const quiet = f.quiet && f.quiet_start && f.quiet_end;
  return {
    name: f.name.trim(),
    event_types: f.event_types,
    client_code: f.client_code || null,
    biz_master_id: f.biz_master_id ?? null,
    template_code: f.template_code,
    destination_ids: f.destination_ids,
    dedupe_window_minutes: f.dedupe_window_minutes || null,
    quiet_start: quiet ? `${f.quiet_start}:00` : null,
    quiet_end: quiet ? `${f.quiet_end}:00` : null,
    quiet_mode: quiet ? f.quiet_mode : null,
    enabled: f.enabled,
  };
}

/** ルールで選べる宛先: 全顧客の宛先と、ルールの顧客の宛先（全顧客のルールなら、どの宛先も。その顧客の出来事のときだけ届く） */
export const selectableDestinations = (all: NotificationDestination[], ruleClient: string | null) =>
  all.filter((d) => !d.is_deleted && (ruleClient === null || d.client_code === null || d.client_code === ruleClient));

// ---------------- エラー ----------------

type Named = { id: number; name: string };
const names = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is Named => !!x && typeof x === "object" && "name" in x).map((x) => `「${x.name}」`) : [];

/**
 * 保存・削除の失敗を日本語にする（サーバーの文は英語。details に理由があるものは、それを使う）。
 * kind: どの操作か（文の主語）
 */
export function settingsErrorText(
  e: { status?: number; code?: string; message?: string; details?: Record<string, unknown> | null },
  kind: "destination" | "rule",
): string {
  const rules = names(e.details?.rules);
  const destinations = names(e.details?.destinations);
  if (e.code === "DELETE_FAILED_RECORD_IN_USE" && rules.length) {
    return `この宛先はルール ${rules.join("・")} で使われているため削除できません。先にルールから外すか、ルールを削除してください。`;
  }
  if (destinations.length) {
    return `このルールの宛先 ${destinations.join("・")} が削除されているため、元に戻せません。先に宛先を元に戻してください。`;
  }
  if (rules.length) {
    return `この宛先を使っているルール ${rules.join("・")} の顧客と合わないため、顧客を変えられません。`;
  }
  if (e.status === 409) return kind === "destination" ? "同じ名前の宛先があります。別の名前にしてください。" : "同じ内容の設定があります。";
  if (e.status === 403) return READ_ONLY_FOR_LIMITED;
  if (e.status === 0) return e.message ?? "通信に失敗しました。";
  return `保存できませんでした: ${e.message ?? "不明なエラー"}`;
}
