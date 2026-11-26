import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv } from "vite";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, "AGENT_LENS_PORT");
  const port = env.AGENT_LENS_PORT || "8080";

  return {
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      proxy: {
        "^/(ready|agents|folders|runs(/[A-Za-z0-9_-]+/(events|stream|continue))?|threads(/by-run/[A-Za-z0-9_-]+|/[A-Fa-f0-9-]+)?)(\\?|$)": `http://127.0.0.1:${port}`,
      },
    },
  };
});
