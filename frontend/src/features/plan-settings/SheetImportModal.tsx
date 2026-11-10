import { InboxOutlined } from "@ant-design/icons";
import { Alert, Button, Descriptions, Modal, Radio, Result, Space, Typography, Upload } from "antd";
import { useEffect, useMemo, useState } from "react";
import { PREVIEW_ONLY_NOTE } from "../../domain/biz/lib/settings";
import { sheetApi } from "../../domain/plan/api";
import type { ReferenceSheet, SheetImportInput, SheetImportResult } from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { parseImportFile, previewImport } from "./sheetImport";

interface Props {
  open: boolean;
  /**
   * 取り込めるか（業務管理者）。false（リリース担当）のときは「確認だけ」: ファイルを選んで、取り込むと何が変わるかを見られるが、
   * 取り込みのボタンは出ない。ここまでの表示はブラウザの中の計算で、サーバーには何も送らない（バックエンドも取り込みを拒否する）
   */
  canImport: boolean;
  /** 選択中の顧客（ファイルの顧客コードと違う場合は取り込ませない） */
  clientCode: string;
  /** 選択中の顧客の今のシート（取り込むと何が変わるかを数えるのに使う） */
  sheets: ReferenceSheet[];
  onClose: () => void;
  /** 取り込みが成功したとき（一覧を読み直す） */
  onImported: () => void;
}

/** nginx の上限（5MB）より小さくしておく。20,000 件の日付でも 1MB ほど */
const MAX_FILE_BYTES = 4 * 1024 * 1024;

/**
 * 年間計画シートを JSON ファイルから取り込む（業務管理者だけ）。
 * ファイルは利用者の側で用意する（元の表からの変換はシステムの外で行う）。ここではファイルを選び、
 * 「追加だけ」か「ファイルに合わせる」かを選んで、何が変わるかを確認してから取り込む。結果の件数は取り込み後に表示する。
 */
export function SheetImportModal({ open, canImport, clientCode, sheets, onClose, onImported }: Props) {
  const [fileName, setFileName] = useState<string | null>(null);
  const [input, setInput] = useState<SheetImportInput | null>(null);
  const [fileProblems, setFileProblems] = useState<string[]>([]);
  const [replaceEvents, setReplaceEvents] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SheetImportResult | null>(null);

  // 開くたびに最初の状態へ戻す
  useEffect(() => {
    if (open) {
      setFileName(null);
      setInput(null);
      setFileProblems([]);
      setReplaceEvents(false);
      setError(null);
      setResult(null);
    }
  }, [open]);

  const preview = useMemo(
    () => (input ? previewImport(input, sheets, clientCode, replaceEvents) : null),
    [input, sheets, clientCode, replaceEvents],
  );

  const readFile = async (file: File) => {
    setFileName(file.name);
    setInput(null);
    setError(null);
    if (file.size > MAX_FILE_BYTES) {
      setFileProblems([`ファイルが大きすぎます（${(file.size / 1024 / 1024).toFixed(1)}MB。上限は 4MB）。`]);
      return;
    }
    const parsed = parseImportFile(await file.text());
    if (parsed.ok) {
      setInput(parsed.input);
      setFileProblems([]);
    } else {
      setFileProblems(parsed.problems);
    }
  };

  const submit = async () => {
    if (!preview || !canImport) return;
    setSubmitting(true);
    setError(null);
    try {
      // 「追加だけ」では、期間を今のシートも含む範囲に広げたものを送る（preview.effective）
      setResult(await sheetApi.importSheet(preview.effective, replaceEvents));
      onImported();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "取り込めませんでした。");
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = input !== null && preview !== null && preview.blockers.length === 0;
  const dateCount = input?.events?.length ?? 0;

  return (
    <Modal
      open={open}
      title={canImport ? "年間計画シートを取り込む" : "年間計画シートの取り込みを確認（取り込みはできません）"}
      width={640}
      onCancel={onClose}
      maskClosable={!submitting}
      footer={
        result || !canImport ? (
          <Button type={canImport ? "primary" : "default"} onClick={onClose}>
            閉じる
          </Button>
        ) : (
          <Space>
            <Button onClick={onClose} disabled={submitting}>
              キャンセル
            </Button>
            <Button type="primary" danger={replaceEvents} disabled={!canSubmit} loading={submitting} onClick={submit}>
              {replaceEvents ? "ファイルに合わせて取り込む" : "取り込む"}
            </Button>
          </Space>
        )
      }
    >
      {result ? (
        <Result
          status="success"
          title={result.created ? `シート「${result.sheet.name}」を作りました` : `シート「${result.sheet.name}」を更新しました`}
          subTitle={
            result.created || result.markers_created + result.markers_updated + result.events_created + result.events_deleted > 0
              ? undefined
              : "ファイルと同じ内容でした（何も変わっていません）。"
          }
          extra={
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="追加したマーカー">{result.markers_created}</Descriptions.Item>
              <Descriptions.Item label="説明を変えたマーカー">{result.markers_updated}</Descriptions.Item>
              <Descriptions.Item label="追加した日付">{result.events_created}</Descriptions.Item>
              <Descriptions.Item label="削除した日付">{result.events_deleted}</Descriptions.Item>
              <Descriptions.Item label="予定を作り直したテンプレート" span={2}>
                {result.templates_regenerated}
              </Descriptions.Item>
            </Descriptions>
          }
        />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {!canImport && <Alert type="info" showIcon message={PREVIEW_ONLY_NOTE} />}
          <Typography.Paragraph type="secondary" className="modal-note" style={{ marginBottom: 0 }}>
            決まった形の JSON ファイル（顧客コード・シート名・期間・マーカー・日付）を取り込みます。顧客コードとシート名が同じシートがあれば更新し、
            無ければ新しく作ります。途中でエラーになった場合は、何も反映されません。
          </Typography.Paragraph>

          <Upload.Dragger
            accept=".json,application/json"
            multiple={false}
            showUploadList={false}
            disabled={submitting}
            beforeUpload={(file) => {
              void readFile(file);
              return false; // ここでは送らない（確認してから取り込む）
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">{fileName ?? "JSON ファイルをここに置くか、クリックして選択"}</p>
            {fileName && <p className="ant-upload-hint">別のファイルにするときは、もう一度選択してください</p>}
          </Upload.Dragger>

          {fileProblems.length > 0 && (
            <Alert
              type="error"
              showIcon
              message="このファイルは取り込めません"
              description={
                <ul className="plain-list">
                  {fileProblems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              }
            />
          )}

          {input && preview && (
            <>
              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label="顧客コード">{input.client_code}</Descriptions.Item>
                <Descriptions.Item label="シート名">
                  {input.sheet_name} {preview.existing ? "（既にあるシートを更新）" : "（新しく作る）"}
                </Descriptions.Item>
                <Descriptions.Item label="ファイルの期間" span={2}>
                  <span className="mono">
                    {input.period_start}〜{input.period_end}
                  </span>
                </Descriptions.Item>
                {preview.existing && (
                  <Descriptions.Item label="シートの期間" span={2}>
                    <span className="mono">
                      {preview.existing.period_start}〜{preview.existing.period_end}
                    </span>
                    {(preview.existing.period_start !== preview.effective.period_start ||
                      preview.existing.period_end !== preview.effective.period_end) && (
                      <>
                        {" → "}
                        <b className="mono">
                          {preview.effective.period_start}〜{preview.effective.period_end}
                        </b>
                        <span className="muted">
                          {preview.periodWidened ? "（今のシートとファイルの両方を含むように広げます）" : "（ファイルの期間に変わります）"}
                        </span>
                      </>
                    )}
                  </Descriptions.Item>
                )}
                <Descriptions.Item label="マーカー">{input.markers.length} 件</Descriptions.Item>
                <Descriptions.Item label="日付">{dateCount} 件</Descriptions.Item>
              </Descriptions>

              {preview.blockers.map((b) => (
                <Alert key={b} type="error" showIcon message={b} />
              ))}

              {preview.existing && (
                <Radio.Group value={replaceEvents} onChange={(e) => setReplaceEvents(e.target.value as boolean)} disabled={submitting}>
                  <Space direction="vertical" size={4}>
                    <Radio value={false}>
                      追加だけ<br />
                      <span className="muted">
                        ファイルにある日付で、まだ無いものを追加します。今ある日付は消しません（新しい年の分だけのファイルも、そのまま足せます）
                      </span>
                    </Radio>
                    <Radio value={true}>
                      ファイルに合わせる<br />
                      <span className="muted">
                        追加に加えて、ファイルに無い日付を削除し、期間もファイルのとおりにします（人が変更・追加・取消した日付は残ります）
                      </span>
                    </Radio>
                  </Space>
                </Radio.Group>
              )}

              {preview.blockers.length === 0 && (
                <Alert
                  type={replaceEvents && preview.toDelete > 0 ? "warning" : "info"}
                  showIcon
                  message={
                    preview.existing
                      ? `取り込むと: 日付を ${preview.toAdd} 件追加${replaceEvents ? `、${preview.toDelete} 件削除` : ""}` +
                        (preview.newMarkers.length > 0 ? `、マーカーを ${preview.newMarkers.length} 件追加` : "")
                      : `取り込むと: マーカー ${preview.newMarkers.length} 件、日付 ${preview.toAdd} 件の新しいシートを作ります`
                  }
                  description={
                    <>
                      {replaceEvents && preview.emptied.length > 0 && (
                        <div>
                          <b>ファイルに日付が 1 件も無いマーカーは、日付がすべて消えます: </b>
                          {preview.emptied.map((m) => `「${m.marker}」（${m.count} 件）`).join("、")}
                          。残したい場合は、そのマーカーの日付もファイルに入れてください。
                        </div>
                      )}
                      {replaceEvents && preview.keptManual > 0 && (
                        <div>ファイルに無くても、人が変更・追加・取消した日付 {preview.keptManual} 件は残ります。</div>
                      )}
                      {preview.existing && (
                        <div>日付が変わったマーカーを参照するテンプレートは、未来の予定が作り直されます。</div>
                      )}
                    </>
                  }
                />
              )}
            </>
          )}

          {error && <Alert type="error" showIcon message="取り込めませんでした（何も反映されていません）" description={error} />}
        </Space>
      )}
    </Modal>
  );
}
