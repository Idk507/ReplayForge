import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const command = process.argv[2];
if (!["dev", "build", "start"].includes(command)) process.exit(2);
const result = spawnSync(process.execPath, [require.resolve("next/dist/bin/next"), command, ...(command === "build" ? [] : ["--hostname", "127.0.0.1"])], {stdio: "inherit", env: {...process.env, NEXT_TELEMETRY_DISABLED: "1"}});
process.exit(result.status ?? 1);
