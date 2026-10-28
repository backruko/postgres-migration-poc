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
