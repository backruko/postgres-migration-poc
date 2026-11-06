import type { Staff } from "../../staff/types";
import type { Client } from "../types";

/**
 * 顧客の画面のルール（表示用。最終的な判定はバックエンド: src/domain_client）。
 * 顧客は削除しない。使わなくなったら無効にする（有効な業務が残っている間は無効にできない）。
 */

/** 顧客の登録・名前の変更・無効 / 有効ができるか（業務管理者） */
export const canManageClients = (s: Staff | null | undefined) => s?.status === "active" && s.roles.includes("admin");

/** 入力された顧客コードを、保存される形（前後の空白なし・大文字）にする */
export const normalizeClientCode = (raw: string) => raw.trim().toUpperCase();

/** 顧客コードの入力の誤り（なければ null）。英数字 4 文字。既にある顧客のコードは使えない */
export function clientCodeError(raw: string, existing: readonly Client[]): string | null {
  const code = normalizeClientCode(raw);
  if (!code) return "顧客コードを入力してください";
  if (!/^[A-Z0-9]{4}$/.test(code)) return "顧客コードは英数字 4 文字です";
  const used = existing.find((c) => c.code === code);
  if (used) return `${code} は「${used.name}」${used.is_active ? "" : "（無効）"}が使っています`;
  return null;
}

/** 無効にできない理由（なければ null）。有効な業務の数はバックエンドも確かめる */
export function deactivateBlockedReason(client: Client, activeBusinessCount: number): string | null {
  if (!client.is_active) return null;
  if (activeBusinessCount > 0) return `有効な業務が ${activeBusinessCount} 件あるため無効にできません（先に業務を削除してください）`;
  return null;
}
