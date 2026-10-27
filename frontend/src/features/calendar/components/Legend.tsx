import { Button } from "antd";
import { describeTemplate, POLICY_LABEL } from "../../../domain/plan/lib/describe";
import type { CalendarTemplateInfo } from "../../../domain/plan/types";
import { chipStyle } from "../lib/chipStyle";
import { Icon } from "./Icon";

interface Props {
  templates: CalendarTemplateInfo[];
  counts: Map<number, number>;
  hidden: Set<number>;
  onToggle: (templateId: number) => void;
  onShowAll: () => void;
}

/** 業務（テンプレート）ごとの凡例。クリックで表示・非表示を切り替える */
export function Legend({ templates, counts, hidden, onToggle, onShowAll }: Props) {
  if (!templates.length) return null;
  const anyHidden = templates.some((t) => hidden.has(t.template_id));

  return (
    <div className="legend" role="group" aria-label="業務で絞り込み">
      <span className="legend-label">業務</span>
      {templates.map((t) => {
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
  );
}
