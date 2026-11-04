# Recording checklist

**Recording order (record screens first, then narrate):**
1. Public app in a clean window: https://replayforge-web.blacksky-3aa3a460.centralindia.azurecontainerapps.io/ (login state).
2. GitHub login → refresh → `/settings/integrations` → select repo → consent → Start an investigation → submit → investigation page. Re-check whether it still sits at CREATED; choose script Version A or B accordingly.
3. `/evaluation` on the public app.
4. `docs/evidence/m2-checkout-e2e/report.json` in the GitHub web view (baseline exit_code 1, candidate exit_code 0).
5. JUnit `m3-azure-es256-v5-durable-acceptance-final.xml` (64 tests), overlay "SYNTHETIC".
6. Repo page (Apache-2.0) and README limitations.

**Pre-flight:** close tabs with secrets; never show `.env`, PEM files, tokens, Azure portal keys; 1080p; English narration.

**Screenshots to keep:** login identity, repo selection with pinned commit, consent, intake, CREATED state (or timeline), `/evaluation`, fixture report.

**Export and verify:** MP4 at or under 2:55. Confirm the real duration in the player (not an estimate). Upload to YouTube as public. Open the link in a logged-out window and confirm it plays and is under 3:00. Paste the URL into Devpost.

**Caption/description:** "ReplayForge — evidence-backed AI incident repair (Nebius × NVIDIA Nemotron). Shows GitHub login, repository consent, investigation intake, the verifier architecture and honest limitations. Code: https://github.com/Idk507/ReplayForge (Apache-2.0)"

Note: no video has been recorded or uploaded by this pass.
