// staff ドメインの型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名で、手書きの型は持たない。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

// ---- 列挙 ----
export type RoleCode = Schemas["RoleCode"];
export type StaffStatus = Schemas["StaffStatus"];

// ---- 職員・ロール ----
export type Role = Schemas["RoleResponse"];
export type Staff = Schemas["StaffResponse"];
export type CreateStaffInput = Schemas["StaffCreate"];
export type UpdateStaffInput = Schemas["StaffUpdate"];
