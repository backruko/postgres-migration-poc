// ワーカーの健康（並行稼働 P.4）の型。すべて OpenAPI から生成した型（shared/api/schema.gen.ts）の別名。
// バックエンドのスキーマが変わったら `npm run gen:api` で再生成する。
import type { components } from "../../../shared/api/schema.gen";

type Schemas = components["schemas"];

/** ワーカーが動いているか（GET /health/worker。ログインなし） */
export type WorkerHealthState = Schemas["WorkerHealthState"];
/** まとめ（GET /worker-health/summary） */
export type WorkerHealthSummary = Schemas["WorkerHealthSummaryResponse"];
export type WorkerHealthDay = Schemas["WorkerHealthDay"];
export type WorkerHealthTotal = Schemas["WorkerHealthTotal"];
export type WorkerStatus = Schemas["WorkerStatus"];
export type WorkerGap = Schemas["WorkerGap"];
export type WorkerUncleanEnd = Schemas["WorkerUncleanEnd"];
/** 受け止めたエラー（GET /worker-health/errors） */
export type WorkerErrorList = Schemas["WorkerErrorListResponse"];
export type WorkerErrorItem = Schemas["WorkerErrorItem"];
