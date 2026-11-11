import { Alert, Collapse, Drawer, Empty, Spin, Table, Tag, Typography } from "antd";
import { useMemo } from "react";
import { templateChangeApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import { useTemplateChangesVersion } from "../../domain/plan/hooks/useTemplateChanges";
import { CHANGE_KIND_LABEL, CHANGE_STATUS_COLOR, CHANGE_STATUS_LABEL } from "../../domain/plan/lib/templateChanges";
import type { PlanTemplate, TemplateChange } from "../../domain/plan/types";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { TIMEZONE } from "../../shared/config";
import { inZone } from "../../shared/lib/datetime";
import { changeSummary, summaryBaseline, type SummaryLine, type SummaryNames } from "./templateDraft";

interface Props {
  open: boolean;
  /** 履歴を見るテンプレート（閉じる間の表示が消えないよう、閉じても渡したままにする） */
  template: PlanTemplate | null;
  clientCode: string;
  onClose: () => void;
}

const when = (iso: string | null | undefined) => (iso ? inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm") : "—");
const who = (name: string | null | undefined, id: number | null) => name ?? (id != null ? `職員 #${id}` : "—");

/** 1 件の見出し: いつ・何を・どうなった・だれが */
function header(c: TemplateChange) {
  return (
    <span className="history-head">
      <span className="mono">{when(c.decided_at ?? c.updated_at)}</span>
      <Tag>{CHANGE_KIND_LABEL[c.kind]}</Tag>
      {c.is_direct ? <Tag color="blue">業務管理者の直接の保存</Tag> : <Tag color={CHANGE_STATUS_COLOR[c.status]}>{CHANGE_STATUS_LABEL[c.status]}</Tag>}
      {c.self_approved && <Tag>自分の下書きを承認</Tag>}
      <span className="history-who">
        {c.is_direct
          ? who(c.decided_by_name, c.decided_by_id)
          : c.status === "draft"
            ? `${who(c.drafted_by_name, c.drafted_by_id)} が書いた`
            : `${who(c.drafted_by_name, c.drafted_by_id)} → ${who(c.decided_by_name, c.decided_by_id)}`}
      </span>
    </span>
  );
}

function body(c: TemplateChange, template: PlanTemplate | null, names: SummaryNames) {
  const baseline = summaryBaseline(c, template);
  const lines: SummaryLine[] | null = c.kind === "update" && !baseline.template ? null : changeSummary(c, baseline.template, names);
  return (
    <>
      {c.decision_note && (
        <Typography.Paragraph type={c.status === "rejected" ? "danger" : undefined} className="modal-note">
          {c.status === "rejected" ? "差し戻しの理由: " : "ひとこと: "}{c.decision_note}
        </Typography.Paragraph>
      )}
      {lines === null ? (
        <Typography.Text type="secondary">変更の前の値はありません。</Typography.Text>
      ) : (
        <Table<SummaryLine>
          rowKey="label"
          size="small"
          pagination={false}
          dataSource={lines}
          columns={[
            { title: "項目", dataIndex: "label", width: 130 },
            { title: baseline.label, dataIndex: "before" },
            { title: c.status === "approved" ? "変更後" : "下書きの内容", dataIndex: "after", className: "draft-after" },
          ]}
        />
      )}
    </>
  );
}

/**
 * テンプレートの変更履歴（Level 2、D.6b）。新しい順。承認された変更・業務管理者の直接の保存・差し戻し・取り下げ・承認待ち。
 * 承認された変更（直接の保存を含む）は、承認の直前の値と比べる（その時に何が変わったか）。
 * 履歴は Level 2 の後の変更から（それより前の変更は記録が無い）。
 */
export function TemplateHistoryDrawer({ open, template, clientCode, onClose }: Props) {
  const version = useTemplateChangesVersion();
  const history = useApiQuery(
    (signal) => templateChangeApi.history(template?.id as number, signal), [template?.id, version], { enabled: open && template != null },
  );
  const calendars = useBusinessCalendars();
  const sheets = useReferenceSheets(clientCode, open);
  const names: SummaryNames = useMemo(() => ({
    calendar: (id) => calendars.data?.find((c) => c.id === id)?.name,
    sheet: (id) => sheets.data?.find((s) => s.id === id)?.name,
  }), [calendars.data, sheets.data]);

  const rows = history.data ?? [];
  return (
    <Drawer open={open} onClose={onClose} width={640} title={template ? `変更履歴: ${template.name}` : "変更履歴"} destroyOnClose>
      {history.error && <Alert type="error" showIcon message={`履歴を取得できませんでした: ${history.error.message}`} />}
      <Typography.Paragraph type="secondary" className="modal-note">
        新しい順。下書きの承認・差し戻し・取り下げと、業務管理者の直接の保存が残ります（下書きと承認の仕組みができた後の変更から）。
      </Typography.Paragraph>
      <Spin spinning={history.loading}>
        {rows.length === 0 ? (
          !history.loading && <Empty description="まだ変更の記録がありません" />
        ) : (
          // 新しい 1 件を開いておく。defaultActiveKey は最初の描画でしか読まれないので、行がそろってから描き、
          // 新しい変更が増えたら key で作り直す（読み込み中に空の Collapse を描くと、どれも開かない）
          <Collapse
            key={rows[0].id}
            size="small"
            defaultActiveKey={[rows[0].id]}
            items={rows.map((c) => ({ key: c.id, label: header(c), children: body(c, template, names) }))}
          />
        )}
      </Spin>
    </Drawer>
  );
}
