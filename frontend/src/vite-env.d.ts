/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 本番ビルド時の API ベースURL（未指定なら同一オリジンの /api） */
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_CLIENT?: string;
  readonly VITE_DEFAULT_HOLIDAY_CALENDAR?: string;
  /** 操作する職員の既定の ID（ログインが入るまでの開発用） */
  readonly VITE_DEFAULT_STAFF_ID?: string;
  readonly VITE_TIMEZONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
