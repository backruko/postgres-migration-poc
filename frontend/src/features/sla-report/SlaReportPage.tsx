import { DownloadOutlined } from "@ant-design/icons";
import { Alert, Button, DatePicker, Empty, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useClients } from "../../domain/client/hooks/useClients";
import { useSlaReport } from "../../domain/ops/hooks/useSlaReport";
import { TIMEZONE } from "../../shared/config";
import { dayjs, todayIn } from "../../shared/lib/datetime";
import { csvFileName, tableRows, toCsv, type SlaTableRow } from "./slaReport";

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** CSV をダウンロードする（ブラウザの中で作る。サーバーは同じ集計を返すだけ） */
function download(text: string, fileName: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * SLA の集計（S.2）。顧客 × 締切の月（日本時間）で、完了・遅れ・期限内率・平均遅れ・未完了の遅れを表にする。
 * 顧客が2つ以上ある月は合計の行を足す。期間と顧客は URL の ?from=&to=&client= に保持する（省略時は今月までの 12 か月・全顧客）。
 * 「CSV を出力」は表示している行（合計を含む）をそのまま出す。
 */
export function SlaReportPage() {
  const [params, setParams] = useSearchParams();
  const thisMonth = todayIn(TIMEZONE).slice(0, 7);
  const to = MONTH.test(params.get("to") ?? "") ? (params.get("to") as string) : thisMonth;
  const from = MONTH.test(params.get("from") ?? "")
    ? (params.get("from") as string)
    : dayjs(`${to}-01`, "YYYY-MM-DD").subtract(11, "month").format("YYYY-MM");
  const clientCode = params.get("client")?.toUpperCase() || null;

  const report = useSlaReport(from, to, clientCode);
  const clients = useClients();
  const rows = useMemo(() => tableRows(report.data?.rows ?? []), [report.data]);

  const select = (next: { from?: string; to?: string; client?: string | null }) => {
    const client = next.client === undefined ? clientCode : next.client;
    setParams({ from: next.from ?? from, to: next.to ?? to, ...(client ? { client } : {}) }, { replace: true });
  };

  const num = (v: number | null, suffix = "") => (v === null ? <span className="muted">—</span> : `${v}${suffix}`);
  const columns: ColumnsType<SlaTableRow> = [
    { title: "月", dataIndex: "month", width: 90, className: "mono" },
    {
      title: "顧客",
      key: "client",
      render: (_, r) => (r.isTotal ? <b>合計</b> : <span><span className="mono">{r.client_code}</span> {r.client_name}</span>),
    },
    { title: "完了", dataIndex: "finished", width: 80, align: "right", className: "mono" },
    { title: "遅れ", dataIndex: "late", width: 80, align: "right", className: "mono" },
    { title: "期限内率", key: "rate", width: 100, align: "right", className: "mono", render: (_, r) => num(r.on_time_rate, "%") },
    { title: "平均遅れ（分）", key: "avg", width: 120, align: "right", className: "mono", render: (_, r) => num(r.avg_minutes_late) },
    {
      title: "未完了の遅れ",
      dataIndex: "open_late",
      width: 110,
      align: "right",
      className: "mono",
      render: (v: number) => (v ? <span className="sla-open-late">{v}</span> : v),
    },
  ];

  const notActive = report.error && (report.error.status === 401 || report.error.status === 403);

  return (
    <div className="wrap">
      <header className="page-header">
        <div>
          <div className="eyebrow">集計</div>
          <h1>SLA</h1>
        </div>
      </header>

      <div className="toolbar-row">
        <Space size={8} wrap>
          <DatePicker.RangePicker
            picker="month"
            allowClear={false}
            value={[dayjs(`${from}-01`, "YYYY-MM-DD"), dayjs(`${to}-01`, "YYYY-MM-DD")]}
            format="YYYY年M月"
            onChange={(range) => range?.[0] && range[1] && select({ from: range[0].format("YYYY-MM"), to: range[1].format("YYYY-MM") })}
          />
          <Select<string>
            showSearch
            allowClear
            placeholder="全顧客"
            style={{ width: 260 }}
            value={clientCode ?? undefined}
            options={(clients.data ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))}
            optionFilterProp="label"
            loading={clients.loading}
            onChange={(code?: string) => select({ client: code ?? null })}
          />
        </Space>
        <Button
          icon={<DownloadOutlined />}
          disabled={!rows.length}
          onClick={() => download(toCsv(rows), csvFileName(from, to, clientCode))}
        >
          CSV を出力
        </Button>
      </div>

      <Typography.Paragraph type="secondary">
        締切の月（日本時間）ごと・顧客ごと。期限内率 = 締切までに完了した割合、平均遅れ = 遅れて完了したものだけの平均（分）、
        未完了の遅れ = まだ完了していないのに締切を過ぎたもの（ほかの数には入れない）。取消は数えない。
      </Typography.Paragraph>

      {notActive ? (
        <Alert type="info" showIcon message="SLA を見るには、ナビの「操作する職員」で有効な職員を選んでください。" />
      ) : report.error ? (
        <Alert type="error" showIcon message={`集計を取得できませんでした: ${report.error.message}`} action={<Button size="small" onClick={report.reload}>再読み込み</Button>} />
      ) : (
        <Table<SlaTableRow>
          rowKey="key"
          columns={columns}
          dataSource={rows}
          loading={report.loading}
          pagination={false}
          size="middle"
          scroll={{ x: 760 }}
          rowClassName={(r) => (r.isTotal ? "sla-total-row" : "")}
          locale={{ emptyText: <Empty description="この期間に締切のあるオーダーはありません" /> }}
        />
      )}
    </div>
  );
}
