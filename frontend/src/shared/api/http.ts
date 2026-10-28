import createClient from "openapi-fetch";
import { getActingStaffId } from "./actingStaff";
import type { components, paths } from "./schema.gen";

/**
 * API のベースURL。
 * - 開発時: 既定の "/api" を Vite のプロキシが FastAPI へ転送する（vite.config.ts）
 * - 本番時: 同一オリジンで "/api" を FastAPI に振り分けるか、VITE_API_BASE_URL に API のURLを指定する
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");

/**
 * OpenAPI から生成した型（schema.gen.ts）で型付けされた HTTP クライアント。
 * パス・パラメータ・リクエスト／レスポンスの形はすべてバックエンドの定義と一致する。
 * 各ドメインの api/ からのみ使い、UI からは直接呼ばない。
 */
export const http = createClient<paths>({ baseUrl: API_BASE });

// 操作している職員をすべてのリクエストに付ける（職員・ロールの判定があるエンドポイントで使われる）
http.use({
  onRequest({ request }) {
    const staffId = getActingStaffId();
    if (staffId !== null) request.headers.set("X-Staff-Id", String(staffId));
    return request;
  },
});

type BusinessErrorBody = components["schemas"]["ErrorResponse"];
type ValidationErrorBody = components["schemas"]["HTTPValidationError"];

/** API エラー。FastAPI の業務エラー（error_code 付き）と入力検証エラー（422）をこの形にそろえる */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const isAbortError = (e: unknown) => e instanceof DOMException && e.name === "AbortError";

function toApiError(body: unknown, status: number): ApiError {
  if (body && typeof body === "object" && "error_code" in body) {
    const b = body as BusinessErrorBody;
    return new ApiError(b.message, status, b.error_code);
  }
  if (body && typeof body === "object" && "detail" in body && Array.isArray((body as ValidationErrorBody).detail)) {
    const detail = (body as ValidationErrorBody).detail ?? [];
    const message = detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join(" / ");
    return new ApiError(message || "入力内容に誤りがあります。", status, "VALIDATION_ERROR");
  }
  return new ApiError(`APIエラー (HTTP ${status})`, status, "HTTP_ERROR");
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/** openapi-fetch の結果からデータを取り出す。失敗時は ApiError を投げる */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request;
  } catch (e) {
    if (isAbortError(e)) throw e;
    throw new ApiError("APIサーバーに接続できません。FastAPI が起動しているか確認してください。", 0, "NETWORK_ERROR");
  }
  if (!result.response.ok || result.error !== undefined) throw toApiError(result.error, result.response.status);
  return result.data as T;
}
