// ops（業務横断の表示・操作）の型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名で、手書きの型は持たない。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

// ---- 列挙 ----
export type VoidReason = Schemas["VoidReason"];

// ---- 日次ダッシュボード ----
export type DailyDashboard = Schemas["DailyDashboardResponse"];
export type DailyCalendarStatus = Schemas["DailyCalendarStatus"];
export type DailyClient = Schemas["DailyClient"];
export type DailyBusiness = Schemas["DailyBusiness"];
export type DailyOccurrence = Schemas["DailyOccurrence"];
export type DailyVoidedItem = Schemas["DailyVoidedItem"];
export type DailyMilestone = Schemas["DailyMilestone"];

// ---- 締切が近い・遅れのオーダー（日付によらず。N.3 / N.4） ----
export type Attention = Schemas["AttentionResponse"];
export type AttentionItem = Schemas["AttentionItem"];

// ---- SLA の集計（顧客 × 締切の月。S.1 / S.2） ----
export type SlaReport = Schemas["SlaResponse"];
export type SlaRow = Schemas["SlaRow"];
