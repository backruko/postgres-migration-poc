import { HistoryOutlined } from "@ant-design/icons";
import { Alert, App, Badge, Button, Empty, Spin } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useClients } from "../../domain/client/hooks/useClients";
import { instanceApi } from "../../domain/plan/api";
import { useCancelledInstances } from "../../domain/plan/hooks/useCancelledInstances";
import { useBusinessCalendars, useClientCalendar } from "../../domain/plan/hooks/useClientCalendar";
import type { ClientCalendar, ClientOccurrence } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { DEFAULT_CLIENT, DEFAULT_HOLIDAY_CALENDAR, TIMEZONE } from "../../shared/config";
import { useNow } from "../../shared/hooks/useNow";
import { addMonths, monthLabel, monthOf, todayIn } from "../../shared/lib/datetime";
import { CancelModal } from "../schedule-actions/CancelModal";
import { CancelledDrawer } from "../schedule-actions/CancelledDrawer";
import { OneOffModal } from "../schedule-actions/OneOffModal";
import { OverrideModal } from "../schedule-actions/OverrideModal";
import { AgendaList } from "./components/AgendaList";
import { DayDetail } from "./components/DayDetail";
import { Legend } from "./components/Legend";
import { MonthGrid } from "./components/MonthGrid";
import type { OccurrenceActions } from "./components/OccurrenceList";
import { Toolbar } from "./components/Toolbar";
import { useHiddenTemplates } from "./hooks/useHiddenTemplates";
import { useUrlState } from "./hooks/useUrlState";

/** 月が変わったときに選択する日: 今日（その月なら）→ 最初に予定のある日 → 1日 */
function defaultSelection(data: ClientCalendar, today: string): string | null {
  if (monthOf(today) === data.month) return today;
  return data.days.find((d) => d.occurrences.length)?.date ?? data.days[0]?.date ?? null;
}

export function CalendarPage() {
  const { message } = App.useApp();
  // 1分ごとに再描画し、終了した予定・過ぎた日の表示を自動で切り替える
  const now = useNow();
  const today = todayIn(TIMEZONE);
  const thisMonth = monthOf(today);

  // 既定は「来月」。URL の ?month= があればそちらを優先
  const [query, update] = useUrlState({ client: DEFAULT_CLIENT, month: addMonths(thisMonth, 1), holidays: DEFAULT_HOLIDAY_CALENDAR });
  const clients = useClients();
  const calendars = useBusinessCalendars();
  const { data, loading, error, reload: reloadCalendar } = useClientCalendar(query.client, query.month, TIMEZONE, query.holidays || null);
  const cancelled = useCancelledInstances(query.client, query.month, TIMEZONE);
  const [cancelledOpen, setCancelledOpen] = useState(false);
  const reloadCancelled = cancelled.reload;

  // 予定を操作したら、カレンダーと取消済みの一覧の両方を読み直す
  const reload = useCallback(() => {
    reloadCalendar();
    reloadCancelled();
  }, [reloadCalendar, reloadCancelled]);
  const { hidden, toggle, showAll } = useHiddenTemplates();

  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    if (data) setSelected((prev) => (prev && monthOf(prev) === data.month ? prev : defaultSelection(data, today)));
  }, [data, today]);

  // ---- 手動操作（変更・取消・臨時追加・元に戻す） ----
  const [editing, setEditing] = useState<ClientOccurrence | null>(null);
  const [cancelling, setCancelling] = useState<ClientOccurrence | null>(null);
  const [addingOn, setAddingOn] = useState<string | null>(null);

  const actions: OccurrenceActions = useMemo(
    () => ({
      onEdit: setEditing,
      onCancel: setCancelling,
      onRevert: async (occ) => {
        try {
          const result = await instanceApi.revert(occ.instance_id);
          message.success(result.restored_instance ? "ルールどおりの日時に戻しました" : "手動変更を取り消しました");
          reload();
        } catch (e) {
          message.error(e instanceof ApiError ? e.message : "元に戻せませんでした。");
        }
      },
    }),
    [message, reload],
  );

  // ---- 表示用の集計 ----
  const templates = useMemo(() => new Map((data?.templates ?? []).map((t) => [t.template_id, t])), [data]);
  const visible = useCallback((occ: ClientOccurrence) => !hidden.has(occ.template_id), [hidden]);
  const counts = useMemo(() => {
    const map = new Map<number, number>();
    for (const day of data?.days ?? []) {
      for (const occ of day.occurrences) map.set(occ.template_id, (map.get(occ.template_id) ?? 0) + 1);
    }
    return map;
  }, [data]);
  const visibleDays = (data?.days ?? []).map((d) => d.occurrences.filter(visible).length);
  const visibleTotal = visibleDays.reduce((a, b) => a + b, 0);

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">顧客 業務カレンダー ・ {TIMEZONE}</div>
          <h1>
            <span className="client">{query.client || "—"}</span> {monthLabel(query.month)}
            {loading && <Spin size="small" className="header-spin" />}
          </h1>
        </div>
        {data && (
          <div className="summary">
            <span>予定 <b>{visibleTotal}</b> 件</span>
            <span>予定のある日 <b>{visibleDays.filter((n) => n > 0).length}</b> / {data.days.length} 日</span>
            <span>
              業務 <b>{data.templates.filter((t) => !hidden.has(t.template_id)).length}</b> / {data.templates.length}
            </span>
          </div>
        )}
      </header>

      <div className="toolbar-row">
        <Toolbar
          query={query}
          clients={clients.data ?? []}
          calendars={calendars.data ?? []}
          thisMonth={thisMonth}
          onChange={update}
        />
        {query.client && (
          <Badge count={cancelled.data?.length ?? 0} size="small" offset={[-4, 4]}>
            <Button icon={<HistoryOutlined />} onClick={() => setCancelledOpen(true)}>
              取消済みの予定
            </Button>
          </Badge>
        )}
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          message={error.message}
          action={
            error.status !== 404 && error.status !== 422 ? (
              <Button size="small" onClick={reload}>再読み込み</Button>
            ) : undefined
          }
        />
      )}
      {!query.client && <Empty description="顧客を選択してください" />}

      {data && (
        <>
          {data.templates.length === 0 ? (
            <Empty description="この顧客にはまだ予定テンプレートがありません" />
          ) : (
            <Legend templates={data.templates} counts={counts} hidden={hidden} onToggle={toggle} onShowAll={showAll} />
          )}

          <div className={`main${loading ? " is-loading" : ""}`}>
            <MonthGrid
              days={data.days}
              visible={visible}
              selected={selected}
              today={today}
              now={now}
              timezone={TIMEZONE}
              onSelect={setSelected}
            />
            <DayDetail
              day={data.days.find((d) => d.date === selected)}
              visible={visible}
              templates={templates}
              timezone={TIMEZONE}
              now={now}
              today={today}
              actions={actions}
              onAddOneOff={setAddingOn}
            />
          </div>
          <AgendaList
            days={data.days}
            visible={visible}
            templates={templates}
            timezone={TIMEZONE}
            now={now}
            today={today}
            actions={actions}
            onAddOneOff={setAddingOn}
          />
        </>
      )}

      <OverrideModal occurrence={editing} timezone={TIMEZONE} onClose={() => setEditing(null)} onDone={reload} />
      <CancelModal occurrence={cancelling} timezone={TIMEZONE} onClose={() => setCancelling(null)} onDone={reload} />
      <CancelledDrawer
        open={cancelledOpen}
        month={query.month}
        timezone={TIMEZONE}
        items={cancelled.data ?? []}
        loading={cancelled.loading}
        error={cancelled.error}
        onClose={() => setCancelledOpen(false)}
        onRestored={reload}
      />
      <OneOffModal
        date={addingOn}
        templates={data?.templates ?? []}
        timezone={TIMEZONE}
        onClose={() => setAddingOn(null)}
        onDone={reload}
      />

      <footer className="page-footer">
        色・アイコン・太字は各テンプレートの ui_metadata を反映しています。
        データ: GET /plans/clients/{query.client || "{client_code}"}/calendar?month={query.month}
      </footer>
    </div>
  );
}
