import type { RoleCode, Staff } from "../../staff/types";
import type { SignalResult } from "../types";

export const SIGNAL_RESULT_LABEL: Record<SignalResult, string> = {
  matched: "完了にした",
  duplicate: "重複（すでに完了）",
  unmatched: "照合できない",
  ambiguous: "候補が複数",
};

const HANDLERS: RoleCode[] = ["operator", "releaser", "admin"];

/** 照合できなかった信号に対応できるか（作業者・リリース担当・業務管理者。最終判定はバックエンド） */
export const canHandleSignals = (s: Staff | null | undefined) => s?.status === "active" && s.roles.some((r) => HANDLERS.includes(r));
