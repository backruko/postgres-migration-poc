// biz ドメインの型（OpenAPI から生成した型の別名）
import type { components } from "../../../shared/api/schema.gen";

export type BizMaster = components["schemas"]["BizMasterResponse"];
export type BizMasterCreate = components["schemas"]["BizMasterCreate"];

// ---- 業務間の依存関係 ----
export type BizDependency = components["schemas"]["BizDependencyResponse"];
export type BizDependencyCreate = components["schemas"]["BizDependencyCreate"];
export type DependencyMatchRule = components["schemas"]["DependencyMatchRule"];
export type DependencyKind = components["schemas"]["DependencyKind"];

// ---- 業務の設定（リリース・顧客側の出来事） ----
export type BizMasterUpdate = components["schemas"]["BizMasterUpdate"];
export type ReleaseMode = components["schemas"]["ReleaseMode"];
export type ExecutionType = components["schemas"]["ExecutionType"];
export type CompletionMode = components["schemas"]["CompletionMode"];

// ---- SOP バージョン（オーダーに指定できる承認済みのもの） ----
export type SopVersion = components["schemas"]["SOPVersionResponse"];
export type SopState = components["schemas"]["SopState"];

// ---- SOP の画面（M2.4〜）: 版とステップ ----
export type SopVersionDetail = components["schemas"]["SOPVersionDetailResponse"];
export type SopStep = components["schemas"]["SOPStepTemplateResponse"];
export type SopStepCreate = components["schemas"]["SOPStepTemplateCreate"];
export type SopStepUpdate = components["schemas"]["SOPStepTemplateUpdate"];
