import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the API runs separately (`python -m clipper serve`) on port 8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true },
});
