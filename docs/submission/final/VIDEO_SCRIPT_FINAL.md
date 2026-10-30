# Video script — target 2:40 (hard limit 3:00)

**Version B (collection blocked) is the default.** Switch the 1:25 scene to Version A only if Codex provides retained evidence that CREATED → worker claim → events works, and you re-check the app yourself immediately before recording.
Say "live", "historical" and "synthetic" aloud wherever labeled.

| Time | Screen (record real) | Narration |
|---|---|---|
| 0:00–0:18 | Public app landing / login | "AI can write a plausible fix for a production incident. But a plausible patch isn't proof the incident is resolved. ReplayForge investigates a failure, proposes a constrained repair, and builds an evidence trail that can be independently checked before anything is published." |
| 0:18–0:40 | GitHub login, signed in as Idk507, refresh (session persists) | "This is the deployed application on Azure. I sign in with GitHub, and the session survives a refresh." |
| 0:40–1:05 | `/settings/integrations`: repository list, select `replayforge_checkout_poc`, pinned commit `0ab2adb…`, consent | "ReplayForge only sees repositories the GitHub App installation grants. I choose the checkout service, the exact commit is pinned, and consent to read it is explicit." |
| 1:05–1:25 | Start an investigation: incident text (retry after timeout creates duplicate payments); submit | "The incident: a checkout retry after a network timeout can create duplicate payments. Submitting creates a durable investigation owned by my account." |
| **B** 1:25–1:40 | Investigation at CREATED, 0 events | "Honest status: in this deployment the investigation is still waiting for a worker. I'm not claiming a live repair here." |
| **A** 1:25–1:40 | Real event timeline (only with Codex evidence) | "A worker claimed the run and collected the pinned source; events are durable." |
| 1:40–2:05 | Retained `docs/evidence/m2-checkout-e2e/report.json` on GitHub: baseline exit 1, candidate exit 0, model name | "Separately, a retained historical fixture run, using NVIDIA Nemotron through Nebius Token Factory, shows the pattern: the test fails on the original commit, a patch passes, and a clean replay passes. That's a different commit and a standalone run, not a verified verdict on this app." |
| 2:05–2:20 | JUnit / test output (64 passed) with SYNTHETIC overlay | "Verification requires seven gates: baseline reproduced, patched regression, existing suite, clean replay, patch scope, artifact integrity and security, bound to signed evidence. Locally, with synthetic identities, 64 database tests pass, and forged evidence is rejected." |
| 2:20–2:32 | `/evaluation` page | "Evaluation: one fixture available, three unavailable, zero attempts. No success rate, and we won't invent one." |
| 2:32–2:40 | README limitations / repo page | "ReplayForge fails closed. Live seven-gate verification and PR publication aren't finished, and we say so. Source is Apache-2.0 on GitHub." |

Overlay labels: `LIVE`, `HISTORICAL (different commit)`, `SYNTHETIC TEST`, `NOT VERIFIED`. No music. Hide terminals/env files/tokens.
