import { useCallback, useState } from "react";
import { TEMPLATE_CATEGORIES, type TemplateCategory } from "../../../domain/plan/lib/category";

const STORAGE_KEY = "plan-calendar:hidden-categories";

/** 初めて開いたときに非表示にする分類（件数の多い定例は隠し、不定期・スペシャルを目立たせる） */
const DEFAULT_HIDDEN: TemplateCategory[] = ["regular"];

function load(): Set<TemplateCategory> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return new Set(DEFAULT_HIDDEN);
    return new Set((JSON.parse(raw) as string[]).filter((c): c is TemplateCategory => TEMPLATE_CATEGORIES.includes(c as TemplateCategory)));
  } catch {
    return new Set(DEFAULT_HIDDEN);
  }
}

function save(hidden: Set<TemplateCategory>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...hidden]));
  } catch {
    // ストレージが使えない環境では、このタブの間だけ有効
  }
}

/** 凡例で非表示にした分類（ブラウザに記憶する。未設定なら定例を非表示） */
export function useHiddenCategories() {
  const [hidden, setHidden] = useState<Set<TemplateCategory>>(load);

  const toggle = useCallback((category: TemplateCategory) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      save(next);
      return next;
    });
  }, []);

  return { hidden, toggle };
}
