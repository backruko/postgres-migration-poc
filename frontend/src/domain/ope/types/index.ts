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
