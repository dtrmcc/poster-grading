import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In Docker the backend is reachable as http://backend:8000; locally as 127.0.0.1.
const target = process.env.VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": target } },
});
