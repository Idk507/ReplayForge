import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { publicOrigin } from "../lib/public-origin.mjs";

if (process.env.REPLAYFORGE_DEPLOYMENT_MODE !== "PUBLIC_DEPLOYMENT" ||
    process.env.REPLAYFORGE_WEB_AUTH_MODE !== "github" ||
    !publicOrigin(process.env.REPLAYFORGE_PUBLIC_ORIGIN ?? "") ||
    !/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(process.env.REPLAYFORGE_API_URL ?? "")) {
  process.stderr.write("PUBLIC_WEB_CONFIG_INVALID\n");
  process.exit(1);
}
const require = createRequire(import.meta.url);
const child = spawn(process.execPath, [require.resolve("next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", "3000"], {
  stdio: "inherit", env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
});
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => child.kill(signal));
child.on("error", () => { process.stderr.write("PUBLIC_WEB_START_FAILED\n"); process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
