import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 開発時は /api/* を FastAPI へ転送する（ブラウザから見て同一オリジンになるため CORS 設定は不要）
//   ブラウザ: http://localhost:5173/api/plans/clients/MOCK/calendar
//   FastAPI : http://localhost:8000/plans/clients/MOCK/calendar
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  // localhost は Node で IPv6 (::1) に解決されることがあり、127.0.0.1 で待ち受ける uvicorn に届かないため IPv4 を明示
  const target = env.VITE_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          // ライブラリを別ファイルに分け、アプリ側の変更時にブラウザのキャッシュを活かす
          manualChunks: (id) => (id.includes("node_modules") ? "vendor" : undefined),
        },
      },
      chunkSizeWarningLimit: 1000,
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});
