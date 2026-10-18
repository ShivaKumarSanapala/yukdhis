import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API_TARGET = process.env.API_TARGET ?? "http://localhost:8080";

// Local dev proxies the API; on Firebase Hosting /api/** is rewritten to Cloud Run.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Fonts must stay real files: the CSP (default-src 'self') blocks data: URIs for fonts.
  build: {
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        // vendor chunks keep the app entry small and cache well across deploys
        manualChunks(id: string) {
          if (id.includes("node_modules/firebase") || id.includes("node_modules/@firebase")) return "firebase";
          if (/node_modules\/(react-markdown|remark|micromark|mdast|unified|hast|unist|vfile|devlop|property-information|space-separated-tokens|comma-separated-tokens|character-entities|trim-lines|bail|trough|is-plain-obj|zwitch|html-url-attributes|estree-util-is-identifier-name|ccount|markdown-table|longest-streak)/.test(id)) return "markdown";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": API_TARGET,
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true },
    },
  },
});
