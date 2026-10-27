// ui_metadata.icon の名前 → アイコン。新しいアイコン名を使う場合はここに追加する（未登録の名前は丸で表示）
const PATHS: Record<string, string> = {
  star: "M12 2.5l2.9 6 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.2 1.3-6.6L2.5 9.3l6.6-.8z",
  "money-bag":
    "M9 2h6l-1.6 3.2C17.6 7 20.5 11.4 20.5 15.5 20.5 19.6 17 22 12 22s-8.5-2.4-8.5-6.5C3.5 11.4 6.4 7 10.6 5.2zM12 9.5a.9.9 0 0 0-.9.9v.6c-1.2.3-2 1.1-2 2.2 0 1.3 1.1 1.9 2.5 2.2 1 .2 1.3.4 1.3.8s-.4.7-1 .7c-.7 0-1.2-.3-1.4-.9l-1.6.5c.3 1 1.1 1.7 2.2 1.9v.6a.9.9 0 0 0 1.8 0v-.6c1.3-.3 2.1-1.1 2.1-2.3 0-1.4-1.1-2-2.6-2.3-.9-.2-1.2-.4-1.2-.7s.3-.6.9-.6.9.2 1.1.7l1.6-.5c-.3-.9-1-1.5-1.9-1.7v-.6a.9.9 0 0 0-.9-.9z",
  flag: "M5 2h2v20H5zM8 3h11l-2.5 4.5L19 12H8z",
  bell: "M12 22a2.5 2.5 0 0 0 2.4-2h-4.8a2.5 2.5 0 0 0 2.4 2zm7-6V11a7 7 0 0 0-5.5-6.8V3a1.5 1.5 0 0 0-3 0v1.2A7 7 0 0 0 5 11v5l-2 2v1h18v-1z",
  report: "M6 2h9l5 5v15H6zM14 3.5V8h4.5zM9 12h8v1.6H9zm0 3.5h8v1.6H9zm0 3.5h5v1.6H9z",
};

export function Icon({ name, size = 10 }: { name: string; size?: number }) {
  const d = PATHS[name];
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="icon">
      {d ? <path fill="currentColor" d={d} /> : <circle cx="12" cy="12" r="6" fill="currentColor" />}
    </svg>
  );
}
