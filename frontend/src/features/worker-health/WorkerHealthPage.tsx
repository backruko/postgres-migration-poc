import { DownloadOutlined } from "@ant-design/icons";
import { Alert, Button, DatePicker, Descriptions, Empty, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useSearchParams } from "react-router";
import { useWorkerHealthReport } from "../../domain/worker/hooks/useWorkerHealth";
import type { WorkerErrorItem, WorkerHealthDay } from "../../domain/worker/types";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import {
  STATE_COLOR, STATE_LABEL, UNCLEAN_LABEL, csvFileName, dayRows, errorsByJobText, jobLabel, periodProblem, shortTime, toCsv,
  verdictText,
} from "./workerHealth";

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** CSV をダウンロードする（ブラウザの中で作る） */
function download(text: string, fileName: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

const at = (iso: string | null | undefined) => (iso ? dayjs(iso).tz(TIMEZONE).format("YYYY/M/D HH:mm:ss") : "—");
const dash = <span className="muted">—</span>;

/**
 * ワーカーの状態（並行稼働 P.4c）。並行稼働の承認の条件「ワーカーに処理されないエラー・落ちることが無かった」の証拠を見る画面。
 * - いまの状態（動いている / 止まっている）と最後の心拍
 * - 期間（既定は今日までの 14 日。?from=&to=、最大 62 日）の判定と合計、1 日ごとの行（新しい日が上）、CSV
 * - 承認に数える問題 = 仕事のエラー + 5 分を超える心拍の途切れ + きれいに止まらなかった実行。
 *   届かなかった通知・諦めた出来事は「処理済みの問題」として数えずに並べる（それぞれの画面がある）
 * - 受け止めたエラーの一覧（行を開くとトレースバック）
 * ログインした職員なら誰でも見られる（参照のみ）。1 分ごとに読み直す。
 */
export function WorkerHealthPage() {
  const [params, setParams] = useSearchParams();
  const today = todayIn(TIMEZONE);
  const to = DATE.test(params.get("to") ?? "") ? (params.get("to") as string) : today;
  const from = DATE.test(params.get("from") ?? "")
    ? (params.get("from") as string)
    : dayjs(to, "YYYY-MM-DD").subtract(13, "day").format("YYYY-MM-DD");
  const problem = periodProblem(from, to);
  const { summary, errors } = useWorkerHealthReport(from, to, problem === null);
  const data = summary.data;
  const status = data?.status;

  const select = (next: { from: string; to: string }) => setParams({ from: next.from, to: next.to }, { replace: true });

  const dayColumns: ColumnsType<WorkerHealthDay> = [
    { title: "日付", dataIndex: "day", width: 110, className: "mono", render: (d: string) => dayjs(d, "YYYY-MM-DD").format("M/D(dd)") },
    { title: "判定", key: "ok", width: 70, render: (_, d) => <Tag color={d.ok ? "green" : "red"}>{d.ok ? "OK" : "NG"}</Tag> },
    { title: "問題", dataIndex: "problems", width: 64, align: "right", className: "mono", render: (n: number) => (n ? <b className="text-danger">{n}</b> : 0) },
    { title: "回り", dataIndex: "passes", width: 72, align: "right", className: "mono" },
    { title: "稼働率", key: "uptime", width: 84, align: "right", className: "mono", render: (_, d) => (d.uptime_percent === null || d.uptime_percent === undefined ? dash : `${d.uptime_percent}%`) },
    { title: "最長の間隔", key: "gap", width: 96, align: "right", className: "mono", render: (_, d) => (d.longest_gap_minutes === null || d.longest_gap_minutes === undefined ? dash : `${d.longest_gap_minutes} 分`) },
    { title: "最も遅い回り", key: "slow", width: 104, align: "right", className: "mono", render: (_, d) => (d.slowest_pass_ms === null || d.slowest_pass_ms === undefined ? dash : `${(d.slowest_pass_ms / 1000).toFixed(1)} 秒`) },
    { title: "エラー", key: "errors", render: (_, d) => (d.errors ? <span className="text-danger">{d.errors}（{errorsByJobText(d.errors_by_job)}）</span> : 0) },
    { title: "途切れ", key: "gaps", width: 70, align: "right", className: "mono", render: (_, d) => (d.gaps.length ? <b className="text-danger">{d.gaps.length}</b> : 0) },
    { title: "止まり方", key: "unclean", width: 84, align: "right", className: "mono", render: (_, d) => (d.unclean_ends.length ? <b className="text-danger">{d.unclean_ends.length}</b> : 0) },
    { title: "起動", dataIndex: "restarts", width: 60, align: "right", className: "mono" },
    {
      title: "処理済みの問題",
      key: "handled",
      width: 150,
      render: (_, d) => (d.failed_notifications || d.given_up_events
        ? <span className="muted">届かなかった通知 {d.failed_notifications}・諦めた出来事 {d.given_up_events}</span>
        : dash),
    },
  ];

  const dayDetail = (d: WorkerHealthDay) => (
    <div className="worker-day-detail">
      {d.gaps.map((g) => (
        <div key={`gap-${g.start}`}>
          <Tag color="red">途切れ</Tag>
          {at(g.start)} 〜 {g.ongoing ? "今も続いている" : at(g.end)}（{g.minutes} 分）
        </div>
      ))}
      {d.unclean_ends.map((u) => (
        <div key={`unclean-${u.run_id}`}>
          <Tag color="red">止まり方</Tag>
          {at(u.ended_at)} {UNCLEAN_LABEL[u.reason] ?? u.reason}（実行 {u.run_id}・{u.host}）
        </div>
      ))}
    </div>
  );

  const errorColumns: ColumnsType<WorkerErrorItem> = [
    { title: "日時", dataIndex: "occurred_at", width: 170, className: "mono", render: (v: string) => at(v) },
    { title: "仕事", dataIndex: "job", width: 130, render: (job: string) => jobLabel(job) },
    { title: "種類", dataIndex: "error_type", width: 220, className: "mono" },
    { title: "内容", dataIndex: "message", ellipsis: true },
    { title: "マシン", dataIndex: "host", width: 150, className: "mono" },
  ];

  const notActive = summary.error && (summary.error.status === 401 || summary.error.status === 403);

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">確認 ・ 並行稼働</div>
          <h1>ワーカーの状態</h1>
        </div>
        {status && (
          <div className="summary">
            <Tag color={STATE_COLOR[status.state] ?? "red"} className="worker-state-tag">{STATE_LABEL[status.state] ?? status.state}</Tag>
            <span>最後の心拍 <b>{status.last_beat_at ? shortTime(status.last_beat_at, TIMEZONE) : "—"}</b></span>
          </div>
        )}
      </header>

      <div className="toolbar-row">
        <DatePicker.RangePicker
          allowClear={false}
          value={[dayjs(from, "YYYY-MM-DD"), dayjs(to, "YYYY-MM-DD")]}
          format="YYYY/M/D"
          disabledDate={(d) => d.format("YYYY-MM-DD") > today}
          onChange={(range) => range?.[0] && range[1] && select({ from: range[0].format("YYYY-MM-DD"), to: range[1].format("YYYY-MM-DD") })}
        />
        <Button
          icon={<DownloadOutlined />}
          disabled={!data?.days.length}
          onClick={() => data && download(toCsv(data.days, data.total), csvFileName(from, to))}
        >
          CSV を出力
        </Button>
      </div>

      <Typography.Paragraph type="secondary">
        ワーカーは毎分、自動リリース・予定の生成・締切の監視・通知の振り分けと配信を回し、1 回りごとに心拍を残します。
        承認に数える問題 = 仕事のエラー + 5 分を超える心拍の途切れ（再起動をまたいでも。予定した停止も含むので、理由は承認の記録に書く）
        + きれいに止まらなかった実行（落ちた・強制終了）。届かなかった通知・諦めた出来事は「処理済みの問題」として数えません。日付は日本時間。
      </Typography.Paragraph>

      {problem ? (
        <Alert type="warning" showIcon message={problem} />
      ) : notActive ? (
        <Alert type="info" showIcon message="このアカウントではワーカーの状態を見られません（利用開始前・停止中）。" />
      ) : summary.error ? (
        <Alert type="error" showIcon message={`ワーカーの状態を取得できませんでした: ${summary.error.message}`} action={<Button size="small" onClick={summary.reload}>再読み込み</Button>} />
      ) : (
        <>
          {status && (
            <Descriptions size="small" column={{ xs: 1, md: 3 }} bordered className="worker-status">
              <Descriptions.Item label="いま">
                <Tag color={STATE_COLOR[status.state] ?? "red"}>{STATE_LABEL[status.state] ?? status.state}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="最後の心拍">
                {at(status.last_beat_at)}
                {status.seconds_since_beat !== null && status.seconds_since_beat !== undefined && (
                  <span className="muted">（{Math.floor(status.seconds_since_beat / 60)} 分前）</span>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="この実行の起動">
                {at(status.started_at)}{status.host && <span className="muted">（{status.host}）</span>}
              </Descriptions.Item>
            </Descriptions>
          )}
          {data && (
            <Alert
              className="worker-verdict"
              type={data.total.ok ? "success" : "error"}
              showIcon
              message={verdictText(data.total)}
              description={
                <Space size={16} wrap>
                  <span>回り <b>{data.total.passes}</b></span>
                  <span>稼働率 <b>{data.total.uptime_percent ?? "—"}{data.total.uptime_percent !== null && data.total.uptime_percent !== undefined && "%"}</b></span>
                  <span>最長の間隔 <b>{data.total.longest_gap_minutes ?? "—"}</b> 分</span>
                  <span>起動 <b>{data.total.restarts}</b></span>
                  <span className="muted">処理済みの問題: 届かなかった通知 {data.total.failed_notifications}・諦めた出来事 {data.total.given_up_events}</span>
                </Space>
              }
            />
          )}
          <Table<WorkerHealthDay>
            rowKey="day"
            columns={dayColumns}
            dataSource={data ? dayRows(data.days) : []}
            loading={summary.loading}
            pagination={false}
            size="middle"
            scroll={{ x: 1100 }}
            expandable={{ expandedRowRender: dayDetail, rowExpandable: (d) => d.gaps.length + d.unclean_ends.length > 0 }}
            locale={{ emptyText: <Empty description="この期間の記録はありません" /> }}
          />

          <h2 className="section-title">受け止めたエラー{errors.data && <span className="muted">（{errors.data.total} 件{errors.data.total > errors.data.items.length ? `、新しい ${errors.data.items.length} 件を表示` : ""}）</span>}</h2>
          {errors.error ? (
            <Alert type="error" showIcon message={`エラーの一覧を取得できませんでした: ${errors.error.message}`} />
          ) : (
            <Table<WorkerErrorItem>
              rowKey="id"
              columns={errorColumns}
              dataSource={errors.data?.items ?? []}
              loading={errors.loading}
              pagination={{ pageSize: 20, hideOnSinglePage: true }}
              size="small"
              scroll={{ x: 900 }}
              expandable={{ expandedRowRender: (e) => <pre className="worker-traceback">{e.traceback || e.message}</pre> }}
              locale={{ emptyText: <Empty description="この期間にエラーはありません" /> }}
            />
          )}
        </>
      )}
    </div>
  );
}
