import { Alert, App, Modal, Space, Tabs, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { sheetApi } from "../../domain/plan/api";
import type { ReferenceSheet, ReferenceSheetMarker } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import {
  batchBody, dayKind, defaultYear, emptyChanges, eventsByDate, isPastWithDate, monthsOfYear, toggleDay, yearsOf,
  type CalendarChanges, type CalendarDayKind,
} from "./markerCalendar";
import { monthCells } from "./templatePreview";

interface Props {
  open: boolean;
  sheet: ReferenceSheet;
  marker: ReferenceSheetMarker;
  today: string;
  onClose: () => void;
  onSaved: () => void;
}

const DOW = ["日", "月", "火", "水", "木", "金", "土"];
const KIND_LABEL: Record<CalendarDayKind, string> = {
  saved: "日付あり",
  adding: "足す",
  removing: "消す",
  cancelled: "取り消した日",
  free: "",
  past: "実績（変えられない）",
  outside: "期間の外",
};
const LEGEND: CalendarDayKind[] = ["saved", "adding", "removing", "cancelled"];

/**
 * マーカーの日付を年のカレンダーで足す・消す（E.4）。日をクリックすると「足す」「消す」に入り、もう一度で戻る。
 * 今日より前（実績）と期間の外は変えられない。保存は 1 回（まとめての API）で、参照しているテンプレートの予定は 1 回だけ作り直される。
 * 取り込み・ルールの日付を消すと「取消」の記録が残り、人が足した日付は消える（1 件ずつの削除と同じ）。
 */
export function MarkerCalendarModal({ open, sheet, marker, today, onClose, onSaved }: Props) {
  const { message, modal } = App.useApp();
  const [year, setYear] = useState(() => defaultYear(sheet, today));
  const [changes, setChanges] = useState<CalendarChanges>(emptyChanges);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const events = useMemo(() => eventsByDate(marker), [marker]);

  useEffect(() => {
    if (open) {
      setYear(defaultYear(sheet, today));
      setChanges(emptyChanges());
      setError(null);
      setSaving(false);
    }
  }, [open, sheet, today]);

  const pending = changes.add.size + changes.remove.size;
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await sheetApi.changeEvents(sheet.id, marker.marker, batchBody(changes));
      message.success(
        `「${marker.marker}」の日付を保存しました（${changes.add.size} 日を足し、${changes.remove.size} 日を消しました。参照しているテンプレートの予定も更新しました）`,
      );
      onClose();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  const confirmClose = () => {
    if (!pending || saving) return onClose();
    modal.confirm({
      title: "保存していない変更があります",
      content: `${pending} 日の変更を捨てて閉じますか？`,
      okText: "捨てて閉じる",
      okButtonProps: { danger: true },
      cancelText: "戻る",
      onOk: onClose,
    });
  };

  const months = monthsOfYear(year);
  const empty = new Map();

  return (
    <Modal
      open={open}
      title={`「${marker.marker}」の日付を編集`}
      okText={pending ? `保存する（+${changes.add.size} / −${changes.remove.size}）` : "保存する"}
      okButtonProps={{ disabled: !pending }}
      cancelText="閉じる"
      onOk={() => void save()}
      onCancel={confirmClose}
      confirmLoading={saving}
      maskClosable={false}
      width={980}
      destroyOnClose
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        日をクリックすると「足す」「消す」に入り、もう一度クリックで戻ります。保存するまで何も変わりません。
        今日より前（実績）と期間（{sheet.period_start}〜{sheet.period_end}）の外は変えられません。
        {marker.is_generated && " ルールの日付を消すと「取消」として残り、ルールで作り直されません（「ルールに戻す」で戻せます）。"}
        保存すると、このマーカーを参照しているテンプレートの予定が作り直されます。
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message="保存できませんでした" description={error} className="modal-alert" />}

      <div className="pv-head">
        <Space size="middle" wrap className="pv-legend">
          {LEGEND.map((k) => (
            <span key={k} className="pv-legend-item"><span className={`pv-swatch mc-${k}`} />{KIND_LABEL[k]}</span>
          ))}
          <span className="pv-legend-item"><span className="pv-swatch mc-past-dated" />{KIND_LABEL.past}</span>
        </Space>
        <span className="mono">変更: +{changes.add.size} / −{changes.remove.size}</span>
      </div>

      <Tabs
        size="small"
        activeKey={String(year)}
        onChange={(k) => setYear(Number(k))}
        items={yearsOf(sheet).map((y) => ({ key: String(y), label: `${y}年` }))}
      />

      <div className="pv-months mc-months">
        {months.map((month) => (
          <div key={month} className="pv-month">
            <h4>{Number(month.slice(5))}月</h4>
            <div className="pv-grid pv-dow">
              {DOW.map((d, i) => <span key={d} className={i === 0 ? "is-sun" : i === 6 ? "is-sat" : ""}>{d}</span>)}
            </div>
            <div className="pv-grid">
              {monthCells(month, empty).map((cell, i) => {
                if (!cell.date) return <span key={`b${i}`} className="pv-day blank" />;
                const date = cell.date;
                const kind = dayKind(date, sheet, today, events, changes);
                const clickable = kind !== "past" && kind !== "outside";
                const pastDated = isPastWithDate(date, today, events);
                const label = pastDated ? KIND_LABEL.past : KIND_LABEL[kind];
                return (
                  <button
                    key={date}
                    type="button"
                    className={`pv-day mc-day mc-${kind}${pastDated ? " mc-past-dated" : ""}`}
                    disabled={!clickable}
                    data-date={date}
                    title={label ? `${date} ${label}` : date}
                    aria-label={label ? `${date} ${label}` : date}
                    aria-pressed={kind === "adding" || kind === "removing"}
                    onClick={() => setChanges((c) => toggleDay(date, sheet, today, events, c))}
                  >
                    {cell.dayOfMonth}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
