import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BRIDGE = process.env.BRIDGE_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": BRIDGE,
      "/ws": { target: BRIDGE.replace(/^http/, "ws"), ws: true },
    },
  },
});
