import { useCallback, useState } from "react";

const STORAGE_KEY = "plan-calendar:hidden-templates";

function load(): Set<number> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return new Set(raw ? (JSON.parse(raw) as number[]) : []);
  } catch {
    return new Set();
  }
}

function save(hidden: Set<number>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...hidden]));
  } catch {
    // ストレージが使えない環境では、このタブの間だけ有効
  }
}

/** 凡例で非表示にしたテンプレート（ブラウザに記憶する） */
export function useHiddenTemplates() {
  const [hidden, setHidden] = useState<Set<number>>(load);

  const toggle = useCallback((templateId: number) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(templateId)) next.delete(templateId);
      else next.add(templateId);
      save(next);
      return next;
    });
  }, []);

  const showAll = useCallback(() => {
    const next = new Set<number>();
    save(next);
    setHidden(next);
  }, []);

  return { hidden, toggle, showAll };
}
