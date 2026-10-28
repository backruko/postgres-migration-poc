import { ColorPicker, DatePicker, Form, Space, TimePicker } from "antd";
import { dayjs, todayIn, zonedISO, type Dayjs } from "../../shared/lib/datetime";
import { COLOR_PRESETS, hexFromColorPicker } from "../../shared/ui/color";

/** 変更・臨時追加のフォームで共通の入力値 */
export interface ScheduleFormValues {
  date: Dayjs;
  start: Dayjs;
  end: Dayjs;
  bg_color?: string;
}

/**
 * 日付・開始・終了・表示色の入力欄（同じ日の中での時間帯を入力する）。
 * 予定は未来に向けて立てるため、過去の日付は選べず、今より前の開始時刻はエラーにする（バックエンドでも拒否される）。
 */
export function ScheduleFields({ colorHelp, timezone }: { colorHelp: string; timezone: string }) {
  const today = todayIn(timezone);

  return (
    <>
      <Form.Item name="date" label="日付" rules={[{ required: true, message: "日付を選択してください" }]}>
        <DatePicker
          format="YYYY/MM/DD (dd)"
          allowClear={false}
          disabledDate={(d) => d.format("YYYY-MM-DD") < today}
          style={{ width: 200 }}
        />
      </Form.Item>

      <Form.Item label="時間" required style={{ marginBottom: 0 }}>
        <Space align="start" wrap>
          <Form.Item
            name="start"
            dependencies={["date"]}
            rules={[
              { required: true, message: "開始時刻を選択してください" },
              ({ getFieldValue }) => ({
                validator(_, start?: Dayjs) {
                  const date: Dayjs | undefined = getFieldValue("date");
                  if (!date || !start) return Promise.resolve();
                  return dayjs(zonedISO(date, start, timezone)).isBefore(dayjs())
                    ? Promise.reject(new Error("過去の日時は指定できません"))
                    : Promise.resolve();
                },
              }),
            ]}
          >
            <TimePicker format="HH:mm" minuteStep={5} allowClear={false} placeholder="開始" />
          </Form.Item>
          <span className="range-sep">〜</span>
          <Form.Item
            name="end"
            dependencies={["start"]}
            rules={[
              { required: true, message: "終了時刻を選択してください" },
              ({ getFieldValue }) => ({
                validator(_, end?: Dayjs) {
                  const start: Dayjs | undefined = getFieldValue("start");
                  if (!start || !end) return Promise.resolve();
                  const minutes = (d: Dayjs) => d.hour() * 60 + d.minute();
                  return minutes(end) > minutes(start)
                    ? Promise.resolve()
                    : Promise.reject(new Error("終了は開始より後の時刻にしてください"));
                },
              }),
            ]}
          >
            <TimePicker format="HH:mm" minuteStep={5} allowClear={false} placeholder="終了" />
          </Form.Item>
        </Space>
      </Form.Item>

      <Form.Item
        name="bg_color"
        label="表示色"
        help={colorHelp}
        getValueFromEvent={hexFromColorPicker}
      >
        <ColorPicker format="hex" disabledAlpha presets={COLOR_PRESETS} showText />
      </Form.Item>
    </>
  );
}
