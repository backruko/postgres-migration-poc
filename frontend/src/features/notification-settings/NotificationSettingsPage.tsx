import { EditOutlined, PlusOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, App, Button, Checkbox, Empty, Space, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { notificationSettingsApi } from "../../domain/notifications/api/settingsApi";
import {
  useNotificationDestinations, useNotificationOptions, useNotificationRules,
} from "../../domain/notifications/hooks/useNotificationSettings";
import {
  READ_ONLY_FOR_LIMITED, canEditNotificationSettings, canEditRow, clientText, dedupeText, hasPendingTest, quietText,
  settingsErrorText, testStateOf,
} from "../../domain/notifications/lib/settings";
import type { NotificationDestination, NotificationRule } from "../../domain/notifications/types";
import { useCurrentStaff } from "../../domain/staff/hooks/useStaff";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { dayjs } from "../../shared/lib/datetime";
import { DestinationFormModal } from "./DestinationFormModal";
import { RuleFormModal } from "./RuleFormModal";

const TABS = ["rules", "destinations"] as const;
type TabKey = (typeof TABS)[number];

/** テスト送信の結果を待つ間に読み直す間隔（ワーカーは毎分） */
const TEST_POLL_MS = 5000;

const at = (iso: string) => dayjs(iso).tz(TIMEZONE).format("M/D HH:mm");

/**
 * 通知の設定（N.5c）。タブ: ルール（どの出来事を・どの顧客について・どこへ）/ 宛先（メールアドレス・テスト送信）。
 * 見るのは有効な職員なら誰でも。変えるのは業務管理者だけ（担当顧客が限られた業務管理者は、自分の顧客のものだけ。
 * 全顧客のものは見えるが変えられない）。削除は元に戻せる。タブは URL の ?tab= に保持する。
 */
export function NotificationSettingsPage() {
  const { message, modal } = App.useApp();
  const [params, setParams] = useSearchParams();
  const tab: TabKey = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as TabKey) : "rules";
  const [showDeleted, setShowDeleted] = useState(false);
  const me = useCurrentStaff();
  const canEdit = canEditNotificationSettings(me.data);
  const options = useNotificationOptions();
  const destinations = useNotificationDestinations(showDeleted);
  const rules = useNotificationRules(showDeleted);
  const [destinationEditor, setDestinationEditor] = useState<{ open: boolean; row: NotificationDestination | null }>({ open: false, row: null });
  const [ruleEditor, setRuleEditor] = useState<{ open: boolean; row: NotificationRule | null }>({ open: false, row: null });

  // テスト送信の結果が出るまで、数秒ごとに読み直す（ワーカーが 1 分以内に送る）
  const pending = hasPendingTest(destinations.data);
  const reloadDestinations = useRef(destinations.reload);
  reloadDestinations.current = destinations.reload;
  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => reloadDestinations.current(), TEST_POLL_MS);
    return () => window.clearInterval(id);
  }, [pending]);

  const reloadAll = () => {
    destinations.reload();
    rules.reload();
  };
  const fail = (e: unknown, kind: "destination" | "rule") =>
    message.error(e instanceof ApiError ? settingsErrorText(e, kind) : "通信に失敗しました。", 8);

  const test = async (d: NotificationDestination) => {
    try {
      await notificationSettingsApi.testDestination(d.id);
      message.success(`「${d.name}」へテスト送信を頼みました。1 分以内に送られ、結果がこの一覧に出ます。`);
      destinations.reload();
    } catch (e) {
      fail(e, "destination");
    }
  };

  const confirmDelete = (kind: "destination" | "rule", row: { id: number; name: string }) =>
    modal.confirm({
      title: `${kind === "destination" ? "宛先" : "ルール"}「${row.name}」を削除しますか？`,
      content: kind === "destination"
        ? "この宛先には送らなくなります。「削除したものも表示」から元に戻せます。"
        : "このルールでは知らせなくなります。もう作った通知と、まだ送っていない配信はそのままです。元に戻せます。",
      okText: "削除する",
      okButtonProps: { danger: true },
      cancelText: "キャンセル",
      onOk: async () => {
        try {
          if (kind === "destination") await notificationSettingsApi.deleteDestination(row.id);
          else await notificationSettingsApi.deleteRule(row.id);
          message.success(`「${row.name}」を削除しました。`);
        } catch (e) {
          fail(e, kind);
        }
        reloadAll();
      },
    });

  const restore = async (kind: "destination" | "rule", row: { id: number; name: string }) => {
    try {
      if (kind === "destination") await notificationSettingsApi.restoreDestination(row.id);
      else await notificationSettingsApi.restoreRule(row.id);
      message.success(`「${row.name}」を元に戻しました。`);
    } catch (e) {
      fail(e, kind);
    }
    reloadAll();
  };

  const toggleRule = async (r: NotificationRule) => {
    try {
      await notificationSettingsApi.updateRule(r.id, {
        name: r.name, event_types: r.event_types, client_code: r.client_code, biz_master_id: r.biz_master_id ?? null,
        template_code: r.template_code, destination_ids: r.destinations.map((d) => d.id),
        dedupe_window_minutes: r.dedupe_window_minutes ?? null, quiet_start: r.quiet_start ?? null, quiet_end: r.quiet_end ?? null,
        quiet_mode: r.quiet_mode ?? null, enabled: !r.enabled,
      });
      message.success(`ルール「${r.name}」を${r.enabled ? "無効" : "有効"}にしました。`);
    } catch (e) {
      fail(e, "rule");
    }
    rules.reload();
  };

  const eventLabel = (code: string) => options.data?.event_types.find((e) => e.code === code)?.label ?? code;
  const stateTag = (enabled: boolean, deleted: boolean) =>
    deleted ? <Tag>削除済み</Tag> : enabled ? <Tag color="green">有効</Tag> : <Tag color="default">無効</Tag>;
  const readOnly = (clientCode: string | null) =>
    canEdit && !canEditRow(me.data, clientCode) ? <Tooltip title={READ_ONLY_FOR_LIMITED}><Typography.Text type="secondary">変更できません</Typography.Text></Tooltip> : null;

  const ruleColumns: ColumnsType<NotificationRule> = [
    { title: "名前", dataIndex: "name" },
    { title: "出来事", key: "types", width: 130, render: (_, r) => r.event_types.map((t) => <Tag key={t} color={t === "OrderLate" ? "red" : "orange"}>{eventLabel(t)}</Tag>) },
    { title: "顧客・業務", key: "scope", width: 170, render: (_, r) => <span><span className="mono">{clientText(r.client_code)}</span>{r.biz_name && <span className="muted"> / {r.biz_name}</span>}</span> },
    { title: "宛先", key: "destinations", render: (_, r) => r.destinations.map((d) => d.name).join("、") },
    {
      title: "重複・時間帯", key: "limits", width: 220,
      render: (_, r) => (
        <div className="rule-limits">
          {dedupeText(r.dedupe_window_minutes) && <div>{dedupeText(r.dedupe_window_minutes)}</div>}
          {quietText(r) && <div>{quietText(r)}</div>}
          {!r.dedupe_window_minutes && !r.quiet_start && <span className="muted">—</span>}
        </div>
      ),
    },
    { title: "状態", key: "state", width: 90, render: (_, r) => stateTag(r.enabled, r.is_deleted) },
    { title: "最終更新", key: "updated", width: 130, render: (_, r) => <span className="muted">{at(r.updated_at)} {r.updated_by?.name ?? ""}</span> },
    ...(canEdit ? [{
      title: "操作", key: "actions", width: 210,
      render: (_: unknown, r: NotificationRule) => readOnly(r.client_code) ?? (
        r.is_deleted ? (
          <Button type="link" size="small" onClick={() => void restore("rule", r)}>元に戻す</Button>
        ) : (
          <Space size={0} wrap>
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setRuleEditor({ open: true, row: r })}>直す</Button>
            <Button type="link" size="small" onClick={() => void toggleRule(r)}>{r.enabled ? "無効にする" : "有効にする"}</Button>
            <Button type="link" size="small" danger onClick={() => confirmDelete("rule", r)}>削除</Button>
          </Space>
        )
      ),
    }] : []),
  ];

  const destinationColumns: ColumnsType<NotificationDestination> = [
    { title: "名前", dataIndex: "name" },
    { title: "メールアドレス", dataIndex: "address", className: "mono" },
    { title: "顧客", key: "client", width: 90, className: "mono", render: (_, d) => clientText(d.client_code) },
    { title: "使っているルール", key: "used", render: (_, d) => (d.used_by.length ? d.used_by.map((r) => r.name).join("、") : <span className="muted">なし</span>) },
    { title: "状態", key: "state", width: 90, render: (_, d) => stateTag(d.enabled, d.is_deleted) },
    {
      title: "最後のテスト送信", key: "test", width: 230,
      render: (_, d) => {
        const s = testStateOf(d.last_test);
        if (!d.last_test || !s) return <span className="muted">まだ</span>;
        return (
          <div className="destination-test">
            <Tag color={s.color}>{s.label}</Tag>
            <span className="muted">{at(d.last_test.requested_at)} {d.last_test.requested_by_name ?? ""}</span>
            {d.last_test.error && <div className="failure-error mono">{d.last_test.error}</div>}
          </div>
        );
      },
    },
    ...(canEdit ? [{
      title: "操作", key: "actions", width: 250,
      render: (_: unknown, d: NotificationDestination) => {
        if (d.is_deleted) return readOnly(d.client_code) ?? <Button type="link" size="small" onClick={() => void restore("destination", d)}>元に戻す</Button>;
        // テスト送信は、自分のルールで使える全顧客の宛先も試せる（変えるのではないため）
        const testButton = (
          <Button type="link" size="small" icon={<SendOutlined />} disabled={!d.enabled} title={d.enabled ? undefined : "無効な宛先には送れません"} onClick={() => void test(d)}>
            テスト送信
          </Button>
        );
        const locked = readOnly(d.client_code);
        return (
          <Space size={0} wrap>
            {testButton}
            {locked ?? (
              <>
                <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setDestinationEditor({ open: true, row: d })}>直す</Button>
                <Button type="link" size="small" danger disabled={d.used_by.length > 0}
                  title={d.used_by.length ? `ルール ${d.used_by.map((r) => `「${r.name}」`).join("・")} で使われているため削除できません` : undefined}
                  onClick={() => confirmDelete("destination", d)}>
                  削除
                </Button>
              </>
            )}
          </Space>
        );
      },
    }] : []),
  ];

  const error = rules.error ?? destinations.error ?? options.error;

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">事前設定</div>
          <h1>通知の設定</h1>
        </div>
      </header>

      {error && <Alert type="error" showIcon message={error.message} action={<Button size="small" onClick={reloadAll}>再読み込み</Button>} />}

      <div className="table-head">
        <Typography.Text type="secondary">
          遅れ・まもなくを、だれに知らせるか。{canEdit ? "" : "変更は業務管理者だけができます。"}
          {me.data && me.data.client_codes.length > 0 && ` 担当の顧客（${me.data.client_codes.join(", ")}）と全顧客の設定を表示しています。`}
        </Typography.Text>
        <Space>
          <Checkbox checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)}>削除したものも表示</Checkbox>
          {canEdit && tab === "rules" && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setRuleEditor({ open: true, row: null })} disabled={!destinations.data}>ルールを作る</Button>
          )}
          {canEdit && tab === "destinations" && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setDestinationEditor({ open: true, row: null })}>宛先を作る</Button>
          )}
        </Space>
      </div>

      <Tabs
        activeKey={tab}
        onChange={(key) => setParams(key === "rules" ? {} : { tab: key }, { replace: true })}
        items={[
          {
            key: "rules",
            label: `ルール（${(rules.data ?? []).filter((r) => !r.is_deleted).length}）`,
            children: (
              <Table<NotificationRule>
                rowKey="id" columns={ruleColumns} dataSource={rules.data ?? []} loading={rules.loading} pagination={false} size="middle"
                scroll={{ x: 1100 }} rowClassName={(r) => (r.is_deleted || !r.enabled ? "is-past-row" : "")}
                locale={{ emptyText: <Empty description="ルールはまだありません。先に宛先を作ってから、ルールを作ります" /> }}
              />
            ),
          },
          {
            key: "destinations",
            label: `宛先（${(destinations.data ?? []).filter((d) => !d.is_deleted).length}）`,
            children: (
              <Table<NotificationDestination>
                rowKey="id" columns={destinationColumns} dataSource={destinations.data ?? []} loading={destinations.loading} pagination={false}
                size="middle" scroll={{ x: 1100 }} rowClassName={(d) => (d.is_deleted || !d.enabled ? "is-past-row" : "")}
                locale={{ emptyText: <Empty description="宛先はまだありません" /> }}
              />
            ),
          },
        ]}
      />

      {canEdit && me.data && (
        <>
          <DestinationFormModal
            open={destinationEditor.open} destination={destinationEditor.row} me={me.data}
            onClose={() => setDestinationEditor((s) => ({ ...s, open: false }))} onSaved={reloadAll}
          />
          <RuleFormModal
            open={ruleEditor.open} rule={ruleEditor.row} me={me.data} options={options.data} destinations={destinations.data ?? []}
            onClose={() => setRuleEditor((s) => ({ ...s, open: false }))} onSaved={reloadAll}
          />
        </>
      )}
    </div>
  );
}
