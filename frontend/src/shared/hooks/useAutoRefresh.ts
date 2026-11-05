import { useEffect, useRef } from "react";

/**
 * 画面を開いている間、一定間隔で refresh を呼ぶ（N.4: 日次ダッシュボードの「まもなく」「遅れ」を手で読み直さなくても出す）。
 * タブが隠れている間は呼ばず、表示に戻ったときにすぐ1回呼ぶ。refresh は最新のものを使う（毎回の描画で変わってよい）。
 */
export function useAutoRefresh(refresh: () => void, intervalMs: number) {
  const ref = useRef(refresh);
  ref.current = refresh;

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") ref.current();
    };
    const id = window.setInterval(tick, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") ref.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs]);
}
