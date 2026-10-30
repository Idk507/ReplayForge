# ReplayForge — final submission package (prepared 2026-10-30)

Deadline: Oct 30, 2026, 22:30 IST. Nothing has been submitted to Devpost by this pass.

Files: `DEVPOST_FINAL_ANSWERS.md` (paste into Devpost), `VIDEO_SCRIPT_FINAL.md`, `VIDEO_RECORDING_CHECKLIST.md`, `DEMO_EVIDENCE_SUMMARY.md`, `KNOWN_LIMITATIONS_FINAL.md`.

## Verified public links (checked 2026-10-30)

| Item | Result |
|---|---|
| App https://replayforge-web.blacksky-3aa3a460.centralindia.azurecontainerapps.io/ | 200, Next.js workbench, shows session check then login |
| `/evaluation`, `/settings/integrations` | 200 |
| Anonymous `/api/runs`, `/api/session`, `/api/auth/session`, `/api/health`, `/runs` | 404 (no anonymous data exposure observed) |
| Security headers | CSP, X-Frame-Options DENY, nosniff, no-store |
| Repo https://github.com/Idk507/ReplayForge | 200, public, `main` at `ffa8cc7` |
| License | GitHub API SPDX `Apache-2.0`; raw LICENSE 200 |
| Fixture https://github.com/Idk507/replayforge_checkout_poc | 200 |
| Static snapshot (not the product) | 200 |
| Video URL | **not yet existing** |

## Checklist

- [x] Public repo, Apache-2.0, README with limitations
- [x] Public app URL responds; anonymous API paths restricted
- [x] Devpost text drafted; limitations disclosed
- [ ] Re-check the stalled investigation (`56f56757`) for events; pick script Version A or B
- [ ] Record video (under 3:00), upload public to YouTube, verify duration and playback logged-out
- [ ] Owner writes/approves tool-feedback fields
- [ ] Owner confirms judges can use the demo (GitHub login needed for workbench; decide whether to add test instructions or rely on `/evaluation`)
- [ ] Paste into Devpost, select track, add video URL, submit before 22:30 IST

## Operator handoff

Remaining actions are yours: record and upload the video (script and checklist are ready), fill in the owner-feedback brackets, and press Submit. If Codex has not restored the worker, use Version B of the script and keep the CREATED-state disclosure in the Devpost text. Not independently re-run here: the 64-test v5 suite and 280-test security suite (figures are from retained reports and the briefing). Codex's worker status was not available to this pass.
