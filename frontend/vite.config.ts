import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: Number(process.env.VITE_PORT || 5173),
    proxy: {
      "/api": process.env.VITE_API_TARGET || "http://127.0.0.1:8797",
      "/runtime": process.env.VITE_API_TARGET || "http://127.0.0.1:8797"
    }
  }
});
