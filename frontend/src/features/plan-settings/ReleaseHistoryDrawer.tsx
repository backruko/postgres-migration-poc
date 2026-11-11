import { Alert, Drawer, Empty, Spin, Table } from "antd";
import { bizApi } from "../../domain/biz/api/bizApi";
import { releaseText } from "../../domain/biz/lib/settings";
import type { BizMaster, BizReleaseChange } from "../../domain/biz/types";
import { TIMEZONE } from "../../shared/config";
import { useApiQuery } from "../../shared/hooks/useApiQuery";
import { inZone } from "../../shared/lib/datetime";

interface Props {
  open: boolean;
  biz: BizMaster;
  /** 保存のたびに変わる値（開いている間に保存したら読み直す） */
  reloadKey: unknown;
  onClose: () => void;
}

const when = (iso: string) => inZone(iso, TIMEZONE).format("YYYY/M/D HH:mm");

/**
 * 業務のリリース設定の変更履歴（P.5）。新しい順。リリース担当の変更も、業務管理者の業務の設定の保存も載る。
 * 2026-11-11 より前の変更は記録が無い。
 */
export function ReleaseHistoryDrawer({ open, biz, reloadKey, onClose }: Props) {
  const changes = useApiQuery((signal) => bizApi.releaseChanges(biz.id, signal), [biz.id, reloadKey], { enabled: open });
  return (
    <Drawer open={open} onClose={onClose} width={560} title={`${biz.business_code} のリリース設定の履歴`} destroyOnClose>
      {changes.error && <Alert type="error" showIcon message={`履歴を取得できませんでした: ${changes.error.message}`} />}
      {changes.loading && !changes.data ? (
        <Spin />
      ) : (
        <Table<BizReleaseChange>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={changes.data ?? []}
          locale={{ emptyText: <Empty description="変更の記録がありません（2026-11-11 より前の変更は記録されていません）" /> }}
          columns={[
            { title: "日時", key: "when", width: 130, className: "mono", render: (_, c) => when(c.changed_at) },
            { title: "変えた人", key: "who", width: 110, render: (_, c) => c.changed_by_name ?? (c.changed_by_id != null ? `職員 #${c.changed_by_id}` : "システム") },
            {
              title: "変更",
              key: "change",
              render: (_, c) => (
                <div>
                  <div className="muted">{releaseText(c.before)}</div>
                  <div>→ {releaseText(c.after)}</div>
                </div>
              ),
            },
          ]}
        />
      )}
    </Drawer>
  );
}
