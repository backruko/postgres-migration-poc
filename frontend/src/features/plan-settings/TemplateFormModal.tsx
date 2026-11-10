import {
  Alert,
  App,
  AutoComplete,
  Button,
  ColorPicker,
  DatePicker,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  TimePicker,
  Typography,
} from "antd";
import { useEffect, useState } from "react";
import type { BizMaster } from "../../domain/biz/types";
import { templateApi } from "../../domain/plan/api";
import { useBusinessCalendars } from "../../domain/plan/hooks/useClientCalendar";
import { useReferenceSheets } from "../../domain/plan/hooks/useReferenceSheets";
import { CATEGORY_LABEL, TEMPLATE_CATEGORIES } from "../../domain/plan/lib/category";
import { DAY_OFFSET_OPTIONS, DEADLINE_MODE_HELP, DEADLINE_MODE_LABEL } from "../../domain/plan/lib/deadline";
import { describeRrule, describeTemplate, POLICY_LABEL, TEMPLATE_TYPE_LABEL } from "../../domain/plan/lib/describe";
import type {
  DeadlineMode,
  HolidayPolicy,
  PlanTemplate,
  TemplateChangePreview,
  TemplateCreatePreview,
  TemplateType,
} from "../../domain/plan/types";
import { ApiError } from "../../shared/api/http";
import { TIMEZONE } from "../../shared/config";
import { COLOR_PRESETS, hexFromColorPicker } from "../../shared/ui/color";
import { Icon, ICON_NAMES } from "../calendar/components/Icon";
import {
  diffDates,
  fromTemplate,
  initialValues,
  RRULE_PRESETS,
  rruleRule,
  toCreateInput,
  toUpdateInput,
  type FormValues,
} from "./templateForm";
import { DEFAULT_PREVIEW_MONTHS } from "./templatePreview";
import { TemplatePreviewPanel } from "./TemplatePreviewPanel";

/** 保存の前の確認（確認したときのフォームの値を持ち、「この内容で保存」はその値を送る） */
type PreviewState =
  | { mode: "create"; values: FormValues; data: TemplateCreatePreview }
  | { mode: "update"; values: FormValues; base: PlanTemplate; data: TemplateChangePreview };

interface Props {
  open: boolean;
  /** テンプレートの業務（ページのドロップダウンで選択中のもの） */
  biz: BizMaster;
  /** 編集するテンプレート。null なら新規作成 */
  template: PlanTemplate | null;
  onClose: () => void;
  /** 作成・保存したとき（一覧の再読み込み用）。一部だけ保存できた場合も呼ぶ */
  onSaved: () => void;
}

/**
 * 予定テンプレートの新規作成・編集（削除は未対応）。種類と業務は作成後に変更できない。
 * 保存の前の確認（Level 1）: 編集で日付に関わる項目を変えたときは、保存の前に必ず「何が変わるか」（予定の違いとオーダーへの影響）を
 * 見せ、そこで保存を確定する。名前・締切・表示だけの変更は、これまでどおりそのまま保存する。新規作成では「予定を確認」で見られる（任意）。
 */
export function TemplateFormModal({ open, biz, template, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  // 編集の差分を取る基準（サーバー上の最新）。複数の API 呼び出しの途中で失敗した場合は、保存できた所まで進める
  const [current, setCurrent] = useState<PlanTemplate | null>(template);
  const isEdit = template !== null;
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [months, setMonths] = useState<number>(DEFAULT_PREVIEW_MONTHS);
  const [checking, setChecking] = useState(false); // 確認を取りに行っている間（新規の「予定を確認」・範囲の変更）

  const templateType = Form.useWatch("template_type", form);
  const holidayPolicy = Form.useWatch("holiday_policy", form);
  const offsetDayUnit = Form.useWatch("offset_day_unit", form);
  const rrule = Form.useWatch("rrule", form);
  const sheetId = Form.useWatch("reference_sheet_id", form);
  const marker = Form.useWatch("reference_event_marker", form);
  const offsetDays = Form.useWatch("offset_days", form);
  const deadlineMode = Form.useWatch("deadline_mode", form);

  const calendars = useBusinessCalendars();
  const sheets = useReferenceSheets(biz.client_code, open && templateType === "reference_sheet_based");
  const selectedSheet = sheets.data?.find((s) => s.id === sheetId);

  // 開くたびに、新規なら初期値・編集ならそのテンプレートの値へ戻す
  // （forceRender でフォームは閉じても残るため、前回の入力や別テンプレートの値を必ず消す）
  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue(template ? fromTemplate(template) : initialValues());
      setCurrent(template);
      setError(null);
      setPreview(null);
      setMonths(DEFAULT_PREVIEW_MONTHS);
    }
  }, [open, template, form]);

  // 振替・スキップ・営業日オフセットには営業日カレンダーが必要（バックエンドの検証と同じ条件）
  const calendarRequired = holidayPolicy !== "as_is" || (templateType === "reference_sheet_based" && offsetDayUnit === "business");

  /**
   * 編集の保存: 本体を PATCH し、指定日の追加・削除は別の API で行う。
   * 呼び出しごとに保存は確定するため、成功するたびに onProgress で基準を最新に進める（再試行で同じ変更を二重に送らない）
   */
  const saveEdit = async (v: FormValues, base: PlanTemplate, onProgress: (t: PlanTemplate) => void) => {
    const { added, removed } = base.template_type === "explicit_dates" ? diffDates(v, base) : { added: [], removed: [] };
    let saved = await templateApi.update(base.id, toUpdateInput(v, base));
    onProgress(saved);
    if (added.length) {
      saved = await templateApi.addDates(base.id, added);
      onProgress(saved);
    }
    for (const d of removed) {
      saved = await templateApi.removeDate(base.id, d);
      onProgress(saved);
    }
    return saved;
  };

  const failure = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

  /** 保存を押したとき。編集は、日付に関わる変更があれば先に確認を見せる（確認の取得に失敗したら保存しない） */
  const onFinish = async (v: FormValues) => {
    if (!current) return save(v);
    setSubmitting(true);
    setError(null);
    let data: TemplateChangePreview;
    try {
      data = await templateApi.previewUpdate(current.id, toUpdateInput(v, current), months);
    } catch (e) {
      setError({ title: "保存できませんでした", detail: failure(e, "変更の内容を確認できませんでした。") });
      setSubmitting(false);
      return;
    }
    if (!data.generation_changed) return save(v); // 名前・締切・表示だけ: 予定は変わらないので、そのまま保存
    setPreview({ mode: "update", values: v, base: current, data });
    setSubmitting(false);
  };

  /** 新規作成の「予定を確認」: 入力を検証してから、できる予定を見せる */
  const previewCreate = async () => {
    let v: FormValues;
    try {
      v = await form.validateFields();
    } catch {
      return; // 入力の誤りはフォームに表示される
    }
    setChecking(true);
    setError(null);
    try {
      setPreview({ mode: "create", values: v, data: await templateApi.previewCreate(toCreateInput(v, biz.id), months) });
    } catch (e) {
      setError({ title: "予定を確認できませんでした", detail: failure(e, "予定を確認できませんでした。") });
    } finally {
      setChecking(false);
    }
  };

  /** 確認の範囲（か月）を変えて取り直す */
  const changeMonths = async (next: number) => {
    setMonths(next);
    if (!preview) return;
    setChecking(true);
    try {
      if (preview.mode === "create") {
        const data = await templateApi.previewCreate(toCreateInput(preview.values, biz.id), next);
        setPreview({ ...preview, data });
      } else {
        const data = await templateApi.previewUpdate(preview.base.id, toUpdateInput(preview.values, preview.base), next);
        setPreview({ ...preview, data });
      }
    } catch (e) {
      message.error(`確認を取り直せませんでした: ${failure(e, "通信に失敗しました。")}`);
    } finally {
      setChecking(false);
    }
  };

  const save = async (v: FormValues) => {
    setSubmitting(true);
    setError(null);
    let wrote = false;
    try {
      if (current) {
        const saved = await saveEdit(v, current, (t) => {
          wrote = true;
          setCurrent(t);
        });
        const regenerated = saved.revision > current.revision;
        message.success(`「${saved.name}」を保存しました${regenerated ? "（未来の予定を作り直しました）" : ""}`);
      } else {
        const created = await templateApi.create(toCreateInput(v, biz.id));
        message.success(`「${created.name}」を作成しました`);
      }
      form.resetFields();
      setPreview(null);
      onClose();
      onSaved();
    } catch (e) {
      const detail = failure(e, `テンプレートを${isEdit ? "保存" : "作成"}できませんでした。`);
      setPreview(null); // エラーはフォームの上に出す
      if (wrote) {
        // 本体の更新後（指定日の追加・削除の途中）で失敗した: 保存できた分を一覧に反映し、残りは再度の保存で送る
        setError({ title: "一部の変更だけ保存されました", detail: `${detail}（もう一度保存すると、残りの変更を反映します）` });
        onSaved();
      } else {
        setError({ title: isEdit ? "保存できませんでした" : "作成できませんでした", detail });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={isEdit ? "予定テンプレートを編集" : "予定テンプレートを新規作成"}
      onCancel={onClose}
      width={640}
      forceRender
      footer={
        <Space>
          <Button onClick={onClose} disabled={submitting}>
            キャンセル
          </Button>
          {!isEdit && (
            <Button onClick={previewCreate} loading={checking && preview === null} disabled={submitting}>
              予定を確認
            </Button>
          )}
          <Button type="primary" onClick={() => form.submit()} loading={submitting && preview === null} disabled={checking}>
            {isEdit ? "保存する" : "作成する"}
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" className="modal-note">
        業務: <span className="mono">{biz.business_code}</span> {biz.name}（顧客 {biz.client_code}）
        {template && (
          <>
            <br />
            テンプレートID <span className="mono">{template.id}</span>・改訂 <span className="mono">{template.revision}</span>。
            時刻・ルール・休日の扱いなどを変えると、未来の予定が作り直されます（過去の予定と、手動で変更した予定はそのまま）。
            その場合は、保存の前に「何が変わるか」の確認が出ます。
          </>
        )}
      </Typography.Paragraph>

      {error && <Alert type="error" showIcon message={error.title} description={error.detail} className="modal-alert" />}

      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional" disabled={submitting}>
        <Form.Item name="template_type" label="種類" rules={[{ required: true }]} extra={isEdit ? "種類は作成後に変更できません" : undefined}>
          <Radio.Group
            disabled={isEdit}
            optionType="button"
            buttonStyle="solid"
            options={(Object.keys(TEMPLATE_TYPE_LABEL) as TemplateType[]).map((t) => ({ value: t, label: TEMPLATE_TYPE_LABEL[t] }))}
          />
        </Form.Item>

        <Form.Item
          name="name"
          label="予定名"
          rules={[
            { required: true, whitespace: true, message: "予定名を入力してください" },
            { max: 100, message: "100文字以内で入力してください" },
          ]}
        >
          <Input placeholder="例: 月次締め準備" maxLength={100} showCount />
        </Form.Item>

        <Form.Item name="category" label="分類" extra="カレンダーでは、定例は最初は非表示です（凡例の「分類」で表示できます）。">
          <Radio.Group options={TEMPLATE_CATEGORIES.map((c) => ({ value: c, label: CATEGORY_LABEL[c] }))} />
        </Form.Item>

        <Space wrap align="start" size="large">
          <Form.Item name="start_time" label="開始時刻" rules={[{ required: true, message: "開始時刻を選択してください" }]}>
            <TimePicker format="HH:mm" minuteStep={5} allowClear={false} />
          </Form.Item>
          <Form.Item
            name="duration_minutes"
            label="所要時間（分）"
            rules={[
              { required: true, message: "所要時間を入力してください" },
              { type: "number", min: 1, message: "1分以上にしてください" },
            ]}
          >
            <InputNumber min={1} precision={0} step={15} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="timezone" label="タイムゾーン" rules={[{ required: true, whitespace: true, message: "タイムゾーンを入力してください" }]}>
            <Input style={{ width: 160 }} />
          </Form.Item>
        </Space>

        {/* ---- 種類ごとの項目 ---- */}
        {templateType === "rrule" && (
          <Space wrap align="start" size="large">
            <Form.Item name="anchor_date" label="起点日" rules={[{ required: true, message: "起点日を選択してください" }]}>
              <DatePicker allowClear={false} />
            </Form.Item>
            <Form.Item
              name="rrule"
              label="繰り返しルール（RRULE）"
              extra={rrule ? `→ ${describeRrule(rrule)}` : "例: FREQ=WEEKLY;BYDAY=MO,WE,FR"}
              rules={[
                { required: true, whitespace: true, message: "RRULE を入力してください" },
                rruleRule,
              ]}
            >
              <AutoComplete
                options={RRULE_PRESETS.map((r) => ({ value: r, label: `${describeRrule(r)}　${r}` }))}
                style={{ width: 320 }}
              />
            </Form.Item>
          </Space>
        )}

        {templateType === "explicit_dates" && (
          <Form.Item name="dates" label="指定日" extra={isEdit ? "複数選択できます。追加・削除した日付は、保存時に反映されます。" : "複数選択できます。"}>
            <DatePicker multiple maxTagCount="responsive" style={{ width: "100%" }} />
          </Form.Item>
        )}

        {templateType === "reference_sheet_based" && (
          <>
            <Space wrap align="start" size="large">
              <Form.Item name="reference_sheet_id" label="年間計画シート" rules={[{ required: true, message: "シートを選択してください" }]}>
                <Select
                  style={{ width: 260 }}
                  loading={sheets.loading}
                  placeholder={`顧客 ${biz.client_code} のシート`}
                  options={(sheets.data ?? []).map((s) => ({ value: s.id, label: `${s.name}（${s.period_start}〜${s.period_end}）` }))}
                  onChange={() => form.setFieldValue("reference_event_marker", undefined)}
                  notFoundContent={sheets.loading ? "読み込み中…" : "この顧客にはシートがありません"}
                />
              </Form.Item>
              <Form.Item
                name="reference_event_marker"
                label="参照するマーカー"
                rules={[{ required: true, message: "マーカーを選択してください" }]}
              >
                <Select
                  style={{ width: 220 }}
                  disabled={!selectedSheet}
                  placeholder={selectedSheet ? "マーカーを選択" : "先にシートを選択"}
                  options={(selectedSheet?.markers ?? []).map((m) => ({
                    value: m.marker,
                    label: `${m.marker}${m.description ? `（${m.description}）` : ""}${m.is_generated ? " ・ 自動生成" : ""}`,
                  }))}
                />
              </Form.Item>
            </Space>
            {sheets.error && <Alert type="warning" showIcon message={sheets.error.message} className="modal-alert" />}
            <Space wrap align="start" size="large">
              <Form.Item
                name="offset_days"
                label="基準日のずれ（日）"
                rules={[{ type: "number", min: -366, max: 366, message: "-366〜366 の範囲で入力してください" }]}
              >
                <InputNumber min={-366} max={366} precision={0} style={{ width: 110 }} />
              </Form.Item>
              <Form.Item name="offset_day_unit" label="日数の数え方">
                <Radio.Group
                  options={[
                    { value: "calendar", label: "暦日" },
                    { value: "business", label: "営業日" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="offset_minutes" label="時刻のずれ（分）">
                <InputNumber precision={0} step={30} style={{ width: 110 }} />
              </Form.Item>
            </Space>
            {marker && (
              <Typography.Paragraph type="secondary" className="modal-note">
                →{" "}
                {describeTemplate({
                  template_type: "reference_sheet_based",
                  rrule: null,
                  reference_event_marker: marker,
                  offset_days: offsetDays ?? 0,
                  offset_day_unit: offsetDayUnit ?? "calendar",
                })}
              </Typography.Paragraph>
            )}
          </>
        )}

        <Divider orientation="left" plain>休日の扱い</Divider>
        <Space wrap align="start" size="large">
          <Form.Item name="holiday_policy" label="非営業日に当たった場合">
            <Select
              style={{ width: 300 }}
              options={(Object.keys(POLICY_LABEL) as HolidayPolicy[]).map((p) => ({ value: p, label: POLICY_LABEL[p] }))}
            />
          </Form.Item>
          <Form.Item
            name="business_calendar_id"
            label="営業日カレンダー"
            dependencies={["holiday_policy", "offset_day_unit", "template_type"]}
            rules={[
              {
                required: calendarRequired,
                message: "休日の振替・スキップや営業日での数え方には、営業日カレンダーが必要です",
              },
            ]}
          >
            <Select
              allowClear
              style={{ width: 240 }}
              loading={calendars.loading}
              placeholder={calendarRequired ? "選択してください" : "なし"}
              options={(calendars.data ?? []).map((c) => ({ value: c.id, label: `${c.name}（${c.code}）` }))}
            />
          </Form.Item>
        </Space>

        <Divider orientation="left" plain>締切</Divider>
        <Form.Item name="deadline_mode" label="締切の決め方">
          <Radio.Group>
            <Space direction="vertical">
              {(Object.keys(DEADLINE_MODE_LABEL) as DeadlineMode[]).map((m) => (
                <Radio key={m} value={m}>{DEADLINE_MODE_LABEL[m]} <span className="muted">— {DEADLINE_MODE_HELP[m]}</span></Radio>
              ))}
            </Space>
          </Radio.Group>
        </Form.Item>
        <Space wrap align="start" size="large">
          {deadlineMode === "offset" && (
            <Form.Item name="deadline_offset_minutes" label="開始から" rules={[{ required: true, message: "分を入力してください" }]}>
              <InputNumber min={1} max={10080} addonAfter="分後" style={{ width: 180 }} />
            </Form.Item>
          )}
          {deadlineMode === "fixed_time" && (
            <>
              <Form.Item name="deadline_day_offset" label="予定日の">
                <Select style={{ width: 120 }} options={DAY_OFFSET_OPTIONS} />
              </Form.Item>
              <Form.Item name="deadline_time" label="締切時刻" rules={[{ required: true, message: "締切時刻を選択してください" }]}>
                <TimePicker format="HH:mm" minuteStep={5} />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="warn_before_minutes"
            label="締切の何分前に「まもなく」を出すか"
            extra="0 = 出さない（必要な予定だけに設定してください）"
          >
            <InputNumber min={0} max={1440} addonAfter="分前" style={{ width: 180 }} />
          </Form.Item>
        </Space>

        <Divider orientation="left" plain>表示（任意）</Divider>
        <Space wrap align="start" size="large">
          <Form.Item name="bg_color" label="表示色" getValueFromEvent={hexFromColorPicker}>
            <ColorPicker format="hex" disabledAlpha presets={COLOR_PRESETS} showText />
          </Form.Item>
          <Form.Item name="icon" label="アイコン">
            <Select
              allowClear
              style={{ width: 160 }}
              placeholder="なし"
              options={ICON_NAMES.map((name) => ({
                value: name,
                label: (
                  <span className="icon-option">
                    <Icon name={name} size={12} /> {name}
                  </span>
                ),
              }))}
            />
          </Form.Item>
          <Form.Item name="is_bold" label="太字" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
      </Form>

      <Modal
        open={preview !== null}
        title={preview?.mode === "update" ? "保存の前の確認: この変更で変わること" : "作成の前の確認: できる予定"}
        width={860}
        onCancel={() => setPreview(null)}
        maskClosable={!submitting}
        footer={
          <Space>
            <Button onClick={() => setPreview(null)} disabled={submitting}>
              戻って直す
            </Button>
            <Button type="primary" loading={submitting} disabled={checking} onClick={() => preview && save(preview.values)}>
              {preview?.mode === "update" ? "この内容で保存する" : "この内容で作成する"}
            </Button>
          </Space>
        }
      >
        {preview?.mode === "create" && (
          <TemplatePreviewPanel
            mode="create"
            preview={preview.data}
            tz={preview.values.timezone?.trim() || TIMEZONE}
            months={months}
            onMonthsChange={changeMonths}
            loading={checking}
          />
        )}
        {preview?.mode === "update" && (
          <TemplatePreviewPanel
            mode="update"
            preview={preview.data}
            pendingDates={
              preview.base.template_type === "explicit_dates"
                ? { added: diffDates(preview.values, preview.base).added.length, removed: diffDates(preview.values, preview.base).removed.length }
                : undefined
            }
            tz={preview.values.timezone?.trim() || TIMEZONE}
            months={months}
            onMonthsChange={changeMonths}
            loading={checking}
          />
        )}
      </Modal>
    </Modal>
  );
}
