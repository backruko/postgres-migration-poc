import type { Staff } from "../../staff/types";
import type { BizMaster, BizMasterUpdate } from "../types";

/**
 * 業務の画面のルール（表示用。最終的な判定はバックエンド: src/domain_biz/services.py）。
 * 業務コード = 顧客コード + 連番 4 桁。連番は自動（顧客の最大の番号 + 1。削除した業務の番号も使い回さない）で、変えられない。
 */

/** 業務の登録・編集・削除・元に戻すができるか（業務管理者） */
export const canManageBusinesses = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");

const MAX_BUSINESS_SEQ = 9999;

/** 次に登録する業務が受け取る業務コード（削除した業務も含めた一覧から）。番号が尽きていれば null */
export function nextBusinessCode(clientCode: string, bizesIncludingDeleted: readonly BizMaster[]): string | null {
  const used = bizesIncludingDeleted.filter((b) => b.client_code === clientCode).map((b) => b.business_seq);
  const next = Math.max(0, ...used) + 1;
  return next > MAX_BUSINESS_SEQ ? null : `${clientCode}${String(next).padStart(4, "0")}`;
}

export interface BizFormValues {
  name?: string;
  manager_email?: string;
  description?: string;
}

/** 空の入力は null（未設定）にそろえる */
const orNull = (v: string | undefined) => {
  const t = (v ?? "").trim();
  return t === "" ? null : t;
};

/** 登録の本文の、業務名・管理者メール・説明 */
export const bizBasics = (v: BizFormValues) => ({
  name: (v.name ?? "").trim(),
  manager_email: orNull(v.manager_email),
  description: orNull(v.description),
});

/** 編集で変わった項目だけの本文（変わっていなければ null） */
export function bizUpdateBody(biz: BizMaster, v: BizFormValues): BizMasterUpdate | null {
  const next = bizBasics(v);
  const body: BizMasterUpdate = {};
  if (next.name !== biz.name) body.name = next.name;
  if (next.manager_email !== (biz.manager_email ?? null)) body.manager_email = next.manager_email;
  if (next.description !== (biz.description ?? null)) body.description = next.description;
  return Object.keys(body).length ? body : null;
}

/** 業務の選択肢（顧客コードごとにグループ化、業務コード順。予定テンプレート・SOP の画面） */
export function bizOptions(bizes: readonly BizMaster[]) {
  const byClient = new Map<string, BizMaster[]>();
  for (const b of [...bizes].sort((a, b) => a.business_code.localeCompare(b.business_code))) {
    byClient.set(b.client_code, [...(byClient.get(b.client_code) ?? []), b]);
  }
  return [...byClient.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([clientCode, list]) => ({
      label: `顧客 ${clientCode}`,
      title: clientCode,
      options: list.map((b) => ({ value: b.id, label: `${b.business_code}　${b.name}` })),
    }));
}
