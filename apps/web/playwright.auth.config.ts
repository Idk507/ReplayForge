import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir:"./auth-tests",timeout:30000,fullyParallel:false,workers:1,
  reporter:[["list"],["junit",{outputFile:"test-results/auth-results.xml"}]],
  use:{baseURL:"http://127.0.0.1:3110",...devices["Desktop Chrome"],channel:"msedge",screenshot:"only-on-failure"},
  webServer:{command:"npm run start",url:"http://127.0.0.1:3110",reuseExistingServer:false,timeout:60000,
    env:{PORT:"3110",REPLAYFORGE_WEB_AUTH_MODE:"github",REPLAYFORGE_API_URL:"http://127.0.0.1:8109"}},
});
