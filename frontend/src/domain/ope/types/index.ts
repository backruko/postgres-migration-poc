// ope ドメイン（オーダー）の型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名で、手書きの型は持たない。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

// ---- 列挙 ----
export type OrderStatus = Schemas["OrderStatus"];
export type OrderEventType = Schemas["OrderEventType"];
export type ConflictAction = Schemas["ConflictAction"];

// ---- オーダー ----
export type OrderDetail = Schemas["OrderDetailResponse"];
export type OrderEvent = Schemas["OrderEventResponse"];
export type OrderSopVersion = Schemas["OrderSopVersion"];

// ---- SOP の版の影響（承認の前に見せる。M2.3 / M2.6） ----
export type SopImpact = Schemas["SopImpactResponse"];
export type SopImpactGroup = Schemas["SopImpactGroup"];
export type SopImpactOrder = Schemas["SopImpactOrder"];

// ---- ステップ ----
export type StepState = Schemas["StepState"];
export type StepEventType = Schemas["StepEventType"];
export type OrderStep = Schemas["OrderStepResponse"];
export type StepEvent = Schemas["StepEventResponse"];

// ---- 依存関係 ----
export type OrderDependency = Schemas["OrderDependencyResponse"];
export type OrderDependent = Schemas["OrderDependentResponse"];

// ---- 信号（顧客側の出来事） ----
export type Signal = Schemas["SignalResponse"];
export type SignalResult = Schemas["SignalResult"];
