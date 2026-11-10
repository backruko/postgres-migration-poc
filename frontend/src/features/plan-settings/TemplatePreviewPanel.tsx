import { Alert, Collapse, Segmented, Spin, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import { ORDER_STATUS_COLOR, ORDER_STATUS_LABEL } from "../../domain/ope/lib/orderActions";
import type { TemplateChangePreview, TemplateCreatePreview } from "../../domain/plan/types";
import { inZone, monthLabel } from "../../shared/lib/datetime";
import {
  describeMarks,
  hasDateChanges,
  mainKind,
  MARK_LABEL,
  marksOfChange,
  marksOfCreate,
  monthCells,
  monthsToShow,
  orderImpact,
  PREVIEW_MONTH_OPTIONS,
  type MarkKind,
} from "./templatePreview";

type Props = (
  | { mode: "create"; preview: TemplateCreatePreview }
  | {
      mode: "update";
      preview: TemplateChangePreview;
      /** 指定日テンプレートで、この確認に含まれない日付の追加・削除の数（保存のときに別に反映される） */
      pendingDates?: { added: number; removed: number };
    }
) & {
  /** 表示に使うタイムゾーン（テンプレートのタイムゾーン） */
  tz: string;
  months: number;
  onMonthsChange: (months: number) => void;
  /** 範囲を変えて取り直している間 */
  loading: boolean;
};

type OrderEffect = TemplateChangePreview["order_effects"][number];

const EFFECT_LABEL: Record<OrderEffect["effect"], string> = {
  rescheduled: "日時が変わる",
  canceled: "取消になる",
  conflict: "矛盾になる（人の判断を待つ）",
};
const EFFECT_COLOR: Record<OrderEffect["effect"], string> = { rescheduled: "orange", canceled: "red", conflict: "volcano" };
const DOW = ["日", "月", "火", "水", "木", "金", "土"];
const LIST_LIMIT = 60;

/**
 * テンプレートの「保存の前の確認」の表示。月ごとの小さなカレンダーに、できる予定（新規）または変更の前後の違い（編集）を色で示し、
 * 編集では追従するオーダーへの影響も出す。ここに出る内容は、バックエンドが実際の保存を試し実行して取り消した結果（何も保存されていない）。
 */
export function TemplatePreviewPanel(props: Props) {
  const { tz, months, onMonthsChange, loading } = props;
  const marks = useMemo(
    () => (props.mode === "create" ? marksOfCreate(props.preview, tz) : marksOfChange(props.preview, tz)),
    [props.mode, props.preview, tz],
  );
  const monthList = monthsToShow(props.preview.range_start, props.preview.range_end, marks, tz);
  const dayTime = (iso: string) => inZone(iso, tz).format("YYYY/M/D(dd) HH:mm");
  const rangeText = `${inZone(props.preview.range_start, tz).format("YYYY/M/D")}〜${inZone(props.preview.range_end, tz)
    .subtract(1, "second")
    .format("YYYY/M/D")}`;
  const legend: MarkKind[] = props.mode === "create" ? ["added"] : ["added", "removed", "moved", "moved_from", "manual", "same"];

  return (
    <Spin spinning={loading}>
      <div className="pv-head">
        <span>
          確認の範囲: <span className="mono">{rangeText}</span>
        </span>
        <Segmented<number>
          size="small"
          value={months}
          onChange={onMonthsChange}
          options={PREVIEW_MONTH_OPTIONS.map((m) => ({ value: m, label: `${m}か月` }))}
          aria-label="確認する範囲"
        />
      </div>

      {props.mode === "create" ? <CreateSummary preview={props.preview} /> : <ChangeSummary {...props} />}

      <div className="pv-legend" aria-hidden="true">
        {legend.map((k) => (
          <span key={k} className="pv-legend-item">
            <span className={`pv-swatch k-${k}`} />
            {props.mode === "create" && k === "added" ? "予定ができる日" : MARK_LABEL[k]}
          </span>
        ))}
      </div>

      <div className="pv-months">
        {monthList.map((month) => (
          <section key={month} className="pv-month" aria-label={monthLabel(month)}>
            <h4>{monthLabel(month)}</h4>
            <div className="pv-grid pv-dow" aria-hidden="true">
              {DOW.map((d, i) => (
                <span key={d} className={i === 0 ? "is-sun" : i === 6 ? "is-sat" : undefined}>
                  {d}
                </span>
              ))}
            </div>
            <div className="pv-grid">
              {monthCells(month, marks).map((cell, i) => {
                if (cell.date === null) return <span key={i} className="pv-day blank" />;
                const kind = mainKind(cell.marks);
                const text = cell.marks.length > 0 ? describeMarks(cell.marks) : undefined;
                return (
                  <span
                    key={i}
                    className={`pv-day${kind ? ` k-${kind}` : ""}`}
                    title={text}
                    aria-label={text ? `${cell.dayOfMonth}日: ${text.replace(/\n/g, "、")}` : undefined}
                  >
                    {cell.dayOfMonth}
                    {cell.marks.length > 1 && <sup>{cell.marks.length}</sup>}
                  </span>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      {props.mode === "update" && <ChangeDetails preview={props.preview} dayTime={dayTime} />}
      {props.mode === "create" && props.preview.occurrences.length > 0 && (
        <Collapse
          size="small"
          className="pv-details"
          items={[
            {
              key: "list",
              label: `できる予定の一覧（${props.preview.occurrences.length} 件）`,
              children: <DateList items={props.preview.occurrences.map((o) => dayTime(o.scheduled_start))} />,
            },
          ]}
        />
      )}
    </Spin>
  );
}

function CreateSummary({ preview }: { preview: TemplateCreatePreview }) {
  const n = preview.occurrences.length;
  if (n === 0) {
    return (
      <Alert
        type="warning"
        showIcon
        className="modal-alert"
        message="この範囲には予定ができません"
        description="ルール・指定日・シートの日付を確かめてください（範囲を広げると先の予定を確認できます）。"
      />
    );
  }
  return (
    <Alert
      type="info"
      showIcon
      className="modal-alert"
      message={`この内容で作ると、範囲の中に予定が ${n} 件でき、オーダーも ${preview.orders_created} 件できます`}
      description="まだ何も保存されていません。"
    />
  );
}

function ChangeSummary({ preview, pendingDates }: Extract<Props, { mode: "update" }>) {
  const impact = orderImpact(preview);
  const serious = preview.removed.length > 0 || impact.canceled > 0 || impact.conflict > 0 || impact.rescheduledReleased > 0;
  const dates = hasDateChanges(preview)
    ? `予定: 増える ${preview.added.length} 件・無くなる ${preview.removed.length} 件・日時が変わる ${preview.moved.length} 件`
    : "予定: この範囲の予定は変わりません";
  return (
    <Alert
      type={serious ? "warning" : "info"}
      showIcon
      className="modal-alert"
      message={dates}
      description={
        <ul className="plain-list">
          {preview.manual_unchanged > 0 && <li>人が変更・追加した予定 {preview.manual_unchanged} 件は、そのまま残ります。</li>}
          {impact.any ? (
            <>
              {impact.rescheduled > 0 && (
                <li>
                  オーダー {impact.rescheduled} 件の日時が変わります
                  {impact.rescheduledReleased > 0 && <b>（うちリリース済み・保留中 {impact.rescheduledReleased} 件）</b>}。
                </li>
              )}
              {impact.canceled > 0 && (
                <li>
                  オーダー {impact.canceled} 件が取消になります
                  {impact.canceledReleased > 0 && <b>（うちリリース済み {impact.canceledReleased} 件）</b>}。
                </li>
              )}
              {impact.conflict > 0 && (
                <li>
                  <b>オーダー {impact.conflict} 件が「矛盾」になります。</b>
                  作業中・保留中のオーダーは自動では動かさず、人の判断を待ちます。
                </li>
              )}
              {impact.created > 0 && <li>オーダーが新しく {impact.created} 件できます。</li>}
            </>
          ) : (
            <li>オーダーへの影響はありません（完了・取消以外のオーダー {impact.total} 件）。</li>
          )}
          {pendingDates && pendingDates.added + pendingDates.removed > 0 && (
            <li>
              指定日の追加 {pendingDates.added} 件・削除 {pendingDates.removed} 件は、この確認には含まれません（保存のときに反映されます）。
            </li>
          )}
          <li className="muted">まだ何も保存されていません。</li>
        </ul>
      }
    />
  );
}

function DateList({ items }: { items: string[] }) {
  return (
    <ul className="pv-list mono">
      {items.slice(0, LIST_LIMIT).map((text) => (
        <li key={text}>{text}</li>
      ))}
      {items.length > LIST_LIMIT && <li className="muted">ほか {items.length - LIST_LIMIT} 件</li>}
    </ul>
  );
}

function ChangeDetails({ preview, dayTime }: { preview: TemplateChangePreview; dayTime: (iso: string) => string }) {
  const columns: ColumnsType<OrderEffect> = [
    { title: "オーダー", dataIndex: "order_id", width: 90, className: "mono", render: (id: number) => `#${id}` },
    {
      title: "今の状態",
      dataIndex: "status",
      width: 110,
      render: (s: OrderEffect["status"]) => <Tag color={ORDER_STATUS_COLOR[s]}>{ORDER_STATUS_LABEL[s]}</Tag>,
    },
    { title: "今の予定", dataIndex: "scheduled_start", className: "mono", render: (v: string) => dayTime(v) },
    {
      title: "保存すると",
      key: "effect",
      render: (_, e) => (
        <>
          <Tag color={EFFECT_COLOR[e.effect]}>{EFFECT_LABEL[e.effect]}</Tag>
          {e.new_scheduled_start && <span className="mono">→ {dayTime(e.new_scheduled_start)}</span>}
          {e.reason && <span className="muted">{e.reason}</span>}
        </>
      ),
    },
  ];
  const items = [
    preview.removed.length > 0 && {
      key: "removed",
      label: `無くなる予定（${preview.removed.length} 件）`,
      children: <DateList items={preview.removed.map((o) => dayTime(o.scheduled_start))} />,
    },
    preview.added.length > 0 && {
      key: "added",
      label: `増える予定（${preview.added.length} 件）`,
      children: <DateList items={preview.added.map((o) => dayTime(o.scheduled_start))} />,
    },
    preview.moved.length > 0 && {
      key: "moved",
      label: `日時が変わる予定（${preview.moved.length} 件）`,
      children: <DateList items={preview.moved.map((m) => `${dayTime(m.before_start)} → ${dayTime(m.after_start)}`)} />,
    },
    preview.order_effects.length > 0 && {
      key: "orders",
      label: `影響するオーダー（${preview.order_effects.length} 件）`,
      children: (
        <Table<OrderEffect>
          rowKey="order_id"
          size="small"
          columns={columns}
          dataSource={preview.order_effects}
          pagination={preview.order_effects.length > 20 ? { pageSize: 20, size: "small", showSizeChanger: false } : false}
          scroll={{ x: 560 }}
        />
      ),
    },
  ].filter((x) => x !== false);
  if (items.length === 0) return null;
  // リリース済み・作業中のオーダーに影響があるときは、オーダーの一覧を最初から開いておく
  const open = preview.order_effects.some((e) => e.status !== "pending") ? ["orders"] : [];
  return <Collapse size="small" className="pv-details" defaultActiveKey={open} items={items} />;
}
