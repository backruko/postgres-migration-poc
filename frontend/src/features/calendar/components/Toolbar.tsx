import { LeftOutlined, RightOutlined } from "@ant-design/icons";
import { Button, DatePicker, Select, Space } from "antd";
import type { Client } from "../../../domain/client/types";
import type { BusinessCalendar } from "../../../domain/plan/types";
import { addMonths, dayjs } from "../../../shared/lib/datetime";
import type { CalendarQuery } from "../hooks/useUrlState";

interface Props {
  query: CalendarQuery;
  clients: Client[];
  calendars: BusinessCalendar[];
  thisMonth: string;
  onChange: (patch: Partial<CalendarQuery>) => void;
}

export function Toolbar({ query, clients, calendars, thisMonth, onChange }: Props) {
  const clientOptions = clients.map((c) => ({ value: c.code, label: `${c.code}　${c.name}` }));
  // URL で指定された顧客が一覧に無い場合も選択状態を表示する
  if (query.client && !clients.some((c) => c.code === query.client)) {
    clientOptions.unshift({ value: query.client, label: query.client });
  }
  const nextMonth = addMonths(thisMonth, 1);

  return (
    <div className="toolbar">
      <label className="field">
        <span className="field-label">顧客</span>
        <Select
          id="client-code"
          showSearch
          placeholder="顧客を選択"
          value={query.client || undefined}
          options={clientOptions}
          optionFilterProp="label"
          onChange={(client: string) => onChange({ client })}
          style={{ minWidth: 220 }}
        />
      </label>

      <Space size={4} className="field" aria-label="表示する月">
        <Button icon={<LeftOutlined />} aria-label="前の月" onClick={() => onChange({ month: addMonths(query.month, -1) })} />
        <DatePicker
          id="month"
          picker="month"
          format="YYYY年M月"
          allowClear={false}
          value={dayjs(`${query.month}-01`)}
          onChange={(d) => d && onChange({ month: d.format("YYYY-MM") })}
          style={{ width: 130 }}
        />
        <Button icon={<RightOutlined />} aria-label="次の月" onClick={() => onChange({ month: addMonths(query.month, 1) })} />
        <Button type="link" disabled={query.month === thisMonth} onClick={() => onChange({ month: thisMonth })}>
          今月
        </Button>
        <Button type="link" disabled={query.month === nextMonth} onClick={() => onChange({ month: nextMonth })}>
          来月
        </Button>
      </Space>

      <label className="field">
        <span className="field-label">休日</span>
        <Select
          id="holiday-calendar"
          allowClear
          placeholder="表示しない"
          value={query.holidays || undefined}
          options={calendars.map((c) => ({ value: c.code, label: `${c.name}（${c.code}）` }))}
          onChange={(code?: string) => onChange({ holidays: code ?? "" })}
          style={{ minWidth: 220 }}
        />
      </label>
    </div>
  );
}
