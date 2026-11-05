import { useLocation, type Location } from "react-router-dom";

/**
 * 「戻る」リンクの行き先。ショートカット（カレンダー → 日次ダッシュボード → 予定テンプレートなど）で画面を開くとき、
 * 開いた側の場所をルーターの state に入れて渡す。ナビから開いた画面には state が無いので、戻るリンクも出ない。
 */
export interface ReturnTo {
  label: string;
  /** パス + クエリ（例: "/daily?date=2026-11-10"） */
  to: string;
  /** 戻ったときに渡す state（戻り先の画面がさらに戻るリンクを持っていれば、それも引き継ぐ） */
  state?: unknown;
}

interface WithReturnTo {
  returnTo?: ReturnTo;
}

/** Link / navigate の state に渡す値 */
export const returnState = (returnTo: ReturnTo): WithReturnTo => ({ returnTo });

/** 今いる場所に戻る ReturnTo。state を省略すると、今の画面の state（その画面の戻るリンク）をそのまま引き継ぐ */
export function returnToHere(location: Location, label: string, state: unknown = location.state): ReturnTo {
  return { label, to: location.pathname + location.search, state };
}

/** この画面を開いたときに渡された戻り先（無ければ null） */
export function useReturnTo(): ReturnTo | null {
  const r = (useLocation().state as WithReturnTo | null)?.returnTo;
  return r && typeof r.label === "string" && typeof r.to === "string" ? r : null;
}
