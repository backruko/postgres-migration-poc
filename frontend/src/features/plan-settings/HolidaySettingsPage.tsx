import { DeleteOutlined, EditOutlined, ImportOutlined, LeftOutlined, PlusOutlined, RightOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { calendarApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useHolidays } from "../../domain/plan/hooks/useHolidays";
import { isSupportedJpHolidayYear, JP_HOLIDAY_YEARS } from "../../domain/plan/lib/jpHolidays";
import type { Holiday } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { HolidayFormModal } from "./HolidayFormModal";
import { describeChange, isEditableHoliday, planJpHolidayImport, toHolidayInputs } from "./holidayForm";

const fmt = (d: string) => dayjs(d, "YYYY-MM-DD").format("YYYY/M/D(dd)");

/**
 * 休日カレンダー（営業日カレンダーの休日）の設定画面。
 * カレンダーと年を選んで休日を一覧し、追加（日本の祝日の一括取り込みを含む）・名前の変更・削除ができる。
 * 今日より前の休日は実績のため読み取り専用（バックエンドも拒否する）。
 * 休日を変えると、このカレンダーを使うテンプレートと生成マーカーの今日以降の日付が作り直される。
 * 選択は URL の ?calendar=<id>&year= に保持する。
 */
export function HolidaySettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const today = todayIn(TIMEZONE);

  const calendars = useBusinessCalendars();
  const parsedId = Number(params.get("calendar"));
  // 未指定なら最初のカレンダー
  const calendarId = Number.isInteger(parsedId) && parsedId > 0 ? parsedId : (calendars.data?.[0]?.id ?? null);
  const calendar = calendars.data?.find((c) => c.id === calendarId) ?? null;
  const parsedYear = Number(params.get("year"));
  const year = Number.isInteger(parsedYear) && parsedYear >= 2000 && parsedYear <= 2100 ? parsedYear : Number(today.slice(0, 4));
  const holidays = useHolidays(calendar ? calendar.id : null);

  const select = (patch: { calendar?: number; year?: number }) =>
    setParams({ calendar: String(patch.calendar ?? calendarId ?? ""), year: String(patch.year ?? year) });

  // 追加・名前変更モーダル（holiday が null なら追加）。閉じる間の表示が切り替わらないよう、閉じても対象は残す
  const [editor, setEditor] = useState<{ open: boolean; holiday: Holiday | null }>({ open: false, holiday: null });

  const confirmDelete = (h: Holiday) =>
    modal.confirm({
      title: `${fmt(h.holiday_date)}${h.name ? `（${h.name}）` : ""} を休日から外しますか？`,
      content: "この日は営業日に戻ります。このカレンダーを使うテンプレートと、ルールで日付を作るマーカーの今日以降の日付が作り直されます（人が変更した日付はそのまま）。",
      okText: "休日から外す",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          message.success(describeChange(await calendarApi.deleteHoliday(calendar!.id, h.holiday_date)), 6);
        } catch (e) {
          message.error(`削除できませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        holidays.reload();
      },
    });

  // 日本の祝日をその年の分まとめて取り込む（今日より前・登録済みの日付は取り込まない）
  const confirmJpImport = () => {
    const plan = planJpHolidayImport(year, new Set((holidays.data ?? []).map((h) => h.holiday_date)), today);
    const skipped = [
      plan.past ? `今日より前の ${plan.past} 件（実績）` : "",
      plan.registered ? `登録済みの ${plan.registered} 件（名前もそのまま）` : "",
    ].filter(Boolean);
    if (!plan.toAdd.length) {
      message.info(`${year}年の日本の祝日 ${plan.total} 件は、すべて取り込み済みか今日より前です。`);
      return;
    }
    modal.confirm({
      title: `${year}年の日本の祝日 ${plan.toAdd.length} 件を「${calendar!.name}」に取り込みますか？`,
      width: 560,
      content: (
        <Space direction="vertical" size={8}>
          <ul className="jp-holiday-list">
            {plan.toAdd.map((h) => (
              <li key={h.date}>
                <span className="mono">{fmt(h.date)}</span> {h.name}
              </li>
            ))}
          </ul>
          {skipped.length > 0 && <span className="muted">取り込まない日付: {skipped.join("、")}</span>}
          <span className="muted">
            祝日は法律の規定から計算しています（春分・秋分の日は近似式）。法改正などで内閣府の発表と異なる場合は、取り込んだ後に手で直してください。
            取り込むと、このカレンダーを使うテンプレートと、ルールで日付を作るマーカーの今日以降の日付が作り直されます。
          </span>
        </Space>
      ),
      okText: "取り込む",
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          message.success(describeChange(await calendarApi.addHolidays(calendar!.id, toHolidayInputs(plan.toAdd))), 6);
        } catch (e) {
          message.error(`取り込めませんでした: ${e instanceof ApiError ? e.message : "通信に失敗しました。"}`);
        }
        holidays.reload();
      },
    });
  };

  const columns: ColumnsType<Holiday> = [
    { title: "日付", dataIndex: "holiday_date", className: "mono", render: (d: string) => fmt(d) },
    { title: "名前", dataIndex: "name", render: (v: string | null) => v ?? <span className="muted">—</span> },
    {
      title: "操作",
      key: "actions",
      width: 220,
      render: (_, h) =>
        !isEditableHoliday(h.holiday_date, today) ? (
          <span className="muted">実績（変更できません）</span>
        ) : (
          <Space size={0}>
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditor({ open: true, holiday: h })}>
              名前を変更
            </Button>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDelete(h)}>
              削除
            </Button>
          </Space>
        ),
    },
  ];

  const all = holidays.data ?? [];
  const rows = all.filter((h) => h.holiday_date.startsWith(`${year}-`));
  const upcoming = rows.filter((h) => isEditableHoliday(h.holiday_date, today)).length;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>休日カレンダー</h1>
        </div>
      </header>

      <div className="toolbar-row">
        <label className="field">
          <span className="field-label">カレンダー</span>
          <Select<number>
            id="calendar"
            value={calendar?.id}
            loading={calendars.loading}
            options={(calendars.data ?? []).map((c) => ({ value: c.id, label: `${c.name}（${c.code}）` }))}
            onChange={(id) => select({ calendar: id })}
            notFoundContent={calendars.loading ? "読み込み中…" : "営業日カレンダーがありません"}
            style={{ width: "min(320px, 100%)" }}
          />
        </label>
        <Space size={4} aria-label="年の切り替え">
          <Button icon={<LeftOutlined />} aria-label="前の年" onClick={() => select({ year: year - 1 })} />
          <Typography.Text strong className="mono year-label">
            {year}年
          </Typography.Text>
          <Button icon={<RightOutlined />} aria-label="次の年" onClick={() => select({ year: year + 1 })} />
        </Space>
      </div>

      {calendars.error && <Alert type="warning" showIcon message={`カレンダーを取得できませんでした: ${calendars.error.message}`} />}
      {holidays.error && (
        <Alert type="error" showIcon message={holidays.error.message} action={<Button size="small" onClick={holidays.reload}>再読み込み</Button>} />
      )}

      {!calendar ? (
        <Empty description={calendars.loading ? "読み込み中…" : "営業日カレンダーを選択してください"} />
      ) : (
        <>
          <div className="table-head">
            <Typography.Text type="secondary">
              {calendar.name} の {year}年の休日 {rows.length} 件（今日以降 {upcoming} 件）・週末:{" "}
              {calendar.weekend_days.map((d) => "月火水木金土日"[d - 1]).join("・") || "なし"}
            </Typography.Text>
            <Space wrap>
              <Button
                icon={<ImportOutlined />}
                onClick={confirmJpImport}
                disabled={!isSupportedJpHolidayYear(year) || holidays.loading || !holidays.data}
                title={
                  isSupportedJpHolidayYear(year)
                    ? `${year}年の日本の祝日（振替休日・国民の休日を含む）を取り込む`
                    : `日本の祝日を計算できるのは ${JP_HOLIDAY_YEARS.min}〜${JP_HOLIDAY_YEARS.max}年です`
                }
              >
                日本の祝日を取り込む
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true, holiday: null })}>
                休日を追加
              </Button>
            </Space>
          </div>
          <Table<Holiday>
            rowKey="holiday_date"
            columns={columns}
            dataSource={rows}
            loading={holidays.loading}
            pagination={false}
            size="middle"
            rowClassName={(h) => (isEditableHoliday(h.holiday_date, today) ? "" : "is-past-row")}
            locale={{ emptyText: <Empty description={`${year}年の休日は登録されていません`} /> }}
          />
          <HolidayFormModal
            open={editor.open}
            calendar={calendar}
            holiday={editor.holiday}
            existing={new Set(all.map((h) => h.holiday_date))}
            today={today}
            onClose={() => setEditor((s) => ({ ...s, open: false }))}
            onSaved={holidays.reload}
          />
        </>
      )}
    </div>
  );
}
