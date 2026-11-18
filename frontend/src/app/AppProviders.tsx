import { App as AntdApp, ConfigProvider, theme } from "antd";
import jaJP from "antd/locale/ja_JP";
import { useEffect, useState, type ReactNode } from "react";
import "../shared/lib/datetime"; // dayjs の日本語ロケール・タイムゾーン設定を読み込む

const FONT_BODY = '"Zen Kaku Gothic New", "Hiragino Sans", "Yu Gothic UI", "Meiryo", system-ui, sans-serif';

function usePrefersDark() {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return dark;
}

/**
 * Ant Design の全体設定。
 * カレンダー本体（styles.css）と同じ色・書体をテーマトークンに渡し、フォームやボタンの見た目をそろえる。
 */
export function AppProviders({ children }: { children: ReactNode }) {
  const dark = usePrefersDark();
  return (
    <ConfigProvider
      locale={jaJP}
      theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: dark ? "#6cc3a8" : "#1f6f5c",
          colorError: dark ? "#f0776d" : "#c8342a",
          colorBgLayout: dark ? "#141917" : "#f6f7f5",
          fontFamily: FONT_BODY,
          borderRadius: 4,
        },
      }}
    >
      <AntdApp>{children}</AntdApp>
    </ConfigProvider>
  );
}
