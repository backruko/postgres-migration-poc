// 画面全体の設定値（.env の VITE_* で上書きできる）

/** 予定の表示・入力に使うタイムゾーン（バックエンドのテンプレート既定値と合わせる） */
export const TIMEZONE = import.meta.env.VITE_TIMEZONE || "Asia/Tokyo";

/** 画面を開いたときの既定の顧客コード（URL の ?client= が優先） */
export const DEFAULT_CLIENT = (import.meta.env.VITE_DEFAULT_CLIENT || "").toUpperCase();

/** 操作する職員の既定の ID（ログインが入るまでの開発用。ナビで選んだ職員がブラウザに記憶され、そちらが優先） */
export const DEFAULT_STAFF_ID = Number(import.meta.env.VITE_DEFAULT_STAFF_ID) || null;

/** 画面を開いたときの既定の休日カレンダー（URL の ?holidays= が優先） */
export const DEFAULT_HOLIDAY_CALENDAR = import.meta.env.VITE_DEFAULT_HOLIDAY_CALENDAR || "";
