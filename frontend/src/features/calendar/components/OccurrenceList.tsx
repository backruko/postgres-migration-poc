import { EditOutlined, StopOutlined, UndoOutlined } from "@ant-design/icons";
import { Button, Popconfirm, Space, Tag, Tooltip } from "antd";
import { describeTemplate, ORIGIN_LABEL, POLICY_LABEL } from "../../../domain/plan/lib/describe";
import type { CalendarTemplateInfo, ClientOccurrence } from "../../../domain/plan/types";
import { timeIn } from "../../../shared/lib/datetime";
import { chipStyle } from "../lib/chipStyle";
import { isPastOccurrence } from "../lib/past";

/** 予定ごとの操作（指定しなければ表示のみ） */
export interface OccurrenceActions {
  onEdit: (occ: ClientOccurrence) => void;
  onCancel: (occ: ClientOccurrence) => void;
  onRevert: (occ: ClientOccurrence) => Promise<void>;
}

interface Props {
  occurrences: ClientOccurrence[];
  templates: Map<number, CalendarTemplateInfo>;
  timezone: string;
  now: number;
  actions?: OccurrenceActions;
}

/** 選択日の明細・狭い画面のリストで使う、予定の詳細表示 */
export function OccurrenceList({ occurrences, templates, timezone, now, actions }: Props) {
  return (
    <ul className="items">
      {occurrences.map((occ) => {
        const t = templates.get(occ.template_id);
        const origin = ORIGIN_LABEL[occ.origin];
        return (
          <li className={isPastOccurrence(occ, now) ? "item past" : "item"} key={occ.instance_id}>
            <span className="item-bar" style={{ background: chipStyle(occ.ui_metadata).background }} />
            <div className="item-body">
              <div className="item-time">
                {timeIn(occ.starts_at, timezone)}–{timeIn(occ.ends_at, timezone)}
              </div>
              <div className="item-title">{occ.title}</div>
              <div className="item-meta">
                <span className="mono">{occ.biz_code}</span>
                <span>{occ.biz_name}</span>
              </div>
              <div className="item-tags">
                {t && <Tag bordered={false}>{describeTemplate(t)}</Tag>}
                {t && <Tag bordered={false}>{POLICY_LABEL[t.holiday_policy]}</Tag>}
                {origin && <Tag color={occ.origin === "one_off" ? "gold" : "green"}>{origin}</Tag>}
                {isPastOccurrence(occ, now) && <Tag bordered={false} className="past-tag">終了</Tag>}
              </div>
              {actions && (
                <Space size={4} className="item-actions">
                  <Button size="small" icon={<EditOutlined />} onClick={() => actions.onEdit(occ)}>
                    変更
                  </Button>
                  <Button size="small" danger icon={<StopOutlined />} onClick={() => actions.onCancel(occ)}>
                    取消
                  </Button>
                  {/* 元に戻せるのは、ルールから生成された予定の手動変更で、元の日時がまだ未来のもの（バックエンドが判定） */}
                  {occ.revertable && (
                    <Popconfirm
                      title="ルールどおりの日時に戻しますか？"
                      description="この予定の手動変更を取り消し、テンプレートのルールで作り直します。"
                      okText="元に戻す"
                      cancelText="やめる"
                      onConfirm={() => actions.onRevert(occ)}
                    >
                      <Tooltip title="手動変更を取り消す">
                        <Button size="small" type="text" icon={<UndoOutlined />}>
                          元に戻す
                        </Button>
                      </Tooltip>
                    </Popconfirm>
                  )}
                </Space>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
