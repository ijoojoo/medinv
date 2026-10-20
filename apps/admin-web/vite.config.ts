import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/identity": {
        target: "http://127.0.0.1:8080",
        // Expose only this application's realm and its theme resources.
        // Administration and the master realm remain on the loopback port.
        bypass(req) {
          const path = (req.url || "").split("?")[0]!;
          if (
            /[%\\\\]/.test(path) ||
            path.split("/").some((part) => part === "." || part === "..") ||
            !/^\/identity\/(realms\/medinv\/|resources\/)/.test(path)
          )
            return false;
        },
      },
      "/auth": { target: "http://127.0.0.1:8001" },
      "/control": { target: "http://127.0.0.1:8001" },
      "/api": { target: "http://127.0.0.1:8000" },
    },
  },
});
