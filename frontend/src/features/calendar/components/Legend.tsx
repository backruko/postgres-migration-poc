import { Button } from "antd";
import { CATEGORY_LABEL, categoryOf, TEMPLATE_CATEGORIES, type TemplateCategory } from "../../../domain/plan/lib/category";
import { describeTemplate, POLICY_LABEL } from "../../../domain/plan/lib/describe";
import type { CalendarTemplateInfo } from "../../../domain/plan/types";
import { chipStyle } from "../lib/chipStyle";
import { Icon } from "./Icon";

interface Props {
  templates: CalendarTemplateInfo[];
  /** テンプレートごとの今月の予定数 */
  counts: Map<number, number>;
  hidden: Set<number>;
  onToggle: (templateId: number) => void;
  onShowAll: () => void;
  hiddenCategories: Set<TemplateCategory>;
  onToggleCategory: (category: TemplateCategory) => void;
}

/**
 * 分類・業務（テンプレート）ごとの凡例。クリックで表示・非表示を切り替える。
 * 非表示にした分類の業務は一覧にも出さない（分類を表示に戻すと、業務ごとの表示・非表示はそのまま戻る）。
 */
export function Legend({ templates, counts, hidden, onToggle, onShowAll, hiddenCategories, onToggleCategory }: Props) {
  if (!templates.length) return null;

  const categoryCounts = new Map<TemplateCategory, number>();
  for (const t of templates) {
    const c = categoryOf(t.ui_metadata);
    categoryCounts.set(c, (categoryCounts.get(c) ?? 0) + (counts.get(t.template_id) ?? 0));
  }
  const shown = templates.filter((t) => !hiddenCategories.has(categoryOf(t.ui_metadata)));
  const anyHidden = shown.some((t) => hidden.has(t.template_id));

  return (
    <div className="legend-groups">
      <div className="legend" role="group" aria-label="分類で絞り込み">
        <span className="legend-label">分類</span>
        {TEMPLATE_CATEGORIES.map((c) => (
          <button
            type="button"
            key={c}
            className="legend-item"
            aria-pressed={!hiddenCategories.has(c)}
            onClick={() => onToggleCategory(c)}
          >
            <span>{CATEGORY_LABEL[c]}</span>
            <span className="mono muted">{categoryCounts.get(c) ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="legend" role="group" aria-label="業務で絞り込み">
        <span className="legend-label">業務</span>
        {shown.length === 0 && <span className="muted">表示中の分類に業務はありません</span>}
        {shown.map((t) => {
          const s = chipStyle(t.ui_metadata);
          return (
            <button
              type="button"
              key={t.template_id}
              className="legend-item"
              aria-pressed={!hidden.has(t.template_id)}
              onClick={() => onToggle(t.template_id)}
              title={`${t.biz_name} ・ ${describeTemplate(t)} ・ ${POLICY_LABEL[t.holiday_policy]}`}
            >
              <span className="swatch" style={{ background: s.background, color: s.color }}>
                <Icon name={s.icon} size={11} />
              </span>
              <span>{t.name}</span>
              <span className="mono muted">{t.biz_code}</span>
              <span className="mono muted">{counts.get(t.template_id) ?? 0}</span>
            </button>
          );
        })}
        {anyHidden && (
          <Button type="link" size="small" onClick={onShowAll}>
            すべて表示
          </Button>
        )}
      </div>
    </div>
  );
}
