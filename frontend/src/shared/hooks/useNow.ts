import { useEffect, useState } from "react";

/** 現在時刻（ミリ秒）。intervalMs ごとに更新され、時間の経過で表示を切り替えたい箇所で使う */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
