import type { RoleCode, Staff, StaffStatus } from "../types";

/**
 * 画面の出し分け用の権限（ボタンを隠す・無効にするだけ。最終的な判定はバックエンドの require_roles）。
 * バックエンドの src/domain_staff/router.py と同じ組み合わせにそろえる。
 */
const STAFF_READERS: RoleCode[] = ["system_admin", "admin", "auditor"];

const isActive = (s: Staff | null | undefined): s is Staff => s?.status === "active";
const hasAny = (s: Staff, roles: RoleCode[]) => s.roles.some((r) => roles.includes(r));

/**
 * 担当する顧客が限られた職員か（client_codes が空なら全顧客）。限られた職員には、ほかの顧客のものは見えず（404）、
 * 全顧客に共通の設定（顧客マスタ・休日カレンダーの変更・職員・API キー）は使えない（403。src/domain_staff/scope.py）
 */
export const isClientLimited = (s: Staff | null | undefined) => (s?.client_codes.length ?? 0) > 0;

/** 全顧客に共通の設定を変えられるか（ロールの判定に加えて、担当顧客が限られていないこと） */
export const handlesAllClients = (s: Staff | null | undefined): s is Staff => isActive(s) && !isClientLimited(s);

/** 担当顧客の表示（例: 「全顧客」「MOCK, YA64」） */
export const clientScopeText = (s: Pick<Staff, "client_codes">) => (s.client_codes.length ? s.client_codes.join(", ") : "全顧客");

/** 職員の一覧を見られるか（職員は全顧客に共通: 担当顧客が限られた職員は見られない） */
export const canReadStaff = (s: Staff | null | undefined) => handlesAllClients(s) && hasAny(s, STAFF_READERS);

/** 職員の登録・変更ができるか */
export const canManageStaff = (s: Staff | null | undefined) => handlesAllClients(s) && hasAny(s, ["system_admin"]);

/**
 * 届かなかったアラームを「確認した」にできるか（日々の運用: 作業者・確認者・リリース担当・業務管理者。
 * 閲覧者・監査担当・システム管理者は見るだけ。バックエンドの src/domain_notification/router.py の ACKNOWLEDGERS と同じ）
 */
const FAILURE_ACKNOWLEDGERS: RoleCode[] = ["operator", "checker", "releaser", "admin"];
export const canConfirmFailure = (s: Staff | null | undefined) => isActive(s) && hasAny(s, FAILURE_ACKNOWLEDGERS);

/**
 * 機械用のロール（API キーを持てる）。system_agent の職員は、これ以外のロールもパスワードも持てない
 * （バックエンドの MACHINE_ROLES / _check_machine_only と同じ。2026-11-12）
 */
export const MACHINE_ROLES: RoleCode[] = ["signal_sender", "system_agent"];

/** API キーを発行できる職員か（機械用のロールを持つ） */
export const canHaveApiKeys = (s: Staff) => s.roles.some((r) => MACHINE_ROLES.includes(r));

/** 読み取りの機械用アカウントか（パスワードを持てない・画面にログインしない） */
export const isAgentAccount = (s: Staff) => s.roles.includes("system_agent");

/** API キーで呼べるもの（表示用。バックエンドの src/domain_staff/auth.py の API_KEY_ROUTES_BY_ROLE と同じ） */
const API_KEY_USES: Partial<Record<RoleCode, string[]>> = {
  signal_sender: ["POST /ope/signals（顧客側の出来事の信号を送る）"],
  system_agent: [
    "GET /ops/attention（まもなく・遅れのオーダー）",
    "GET /ops/schedule-feed（これから始める作業の時刻表。mfa のアダプター用）",
    "GET /ops/daily（日次の予定と状況）",
    "GET /staff/me（キーが使えるかの確認）",
  ],
};

/** この職員の API キーで呼べるもの（持っている機械用のロールの分） */
export const apiKeyUses = (s: Staff): string[] => s.roles.flatMap((r) => API_KEY_USES[r] ?? []);

/** 担当顧客を付けられるか（システム管理者は全顧客を扱う: バックエンドも 400 にする） */
export const canHaveClientScope = (roles: RoleCode[]) => !roles.includes("system_admin");

export const STATUS_LABEL: Record<StaffStatus, string> = {
  pending: "利用開始前",
  active: "有効",
  suspended: "利用停止中",
};

export const STATUS_COLOR: Record<StaffStatus, string> = {
  pending: "gold",
  active: "green",
  suspended: "red",
};
