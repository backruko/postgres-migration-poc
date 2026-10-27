import type { ClientOccurrence } from "../../../domain/plan/types";

/** 終了時刻を過ぎた予定（表示を薄くする対象）。実行中の予定は過去扱いにしない */
export const isPastOccurrence = (occ: Pick<ClientOccurrence, "ends_at">, now: number) =>
  new Date(occ.ends_at).getTime() <= now;
