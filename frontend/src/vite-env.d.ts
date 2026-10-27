/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 本番ビルド時の API ベースURL（未指定なら同一オリジンの /api） */
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_CLIENT?: string;
  readonly VITE_DEFAULT_HOLIDAY_CALENDAR?: string;
  readonly VITE_TIMEZONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
