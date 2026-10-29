# ReplayForge demo narration — 2 minutes 45 seconds

Recording draft, updated 2026-10-29. This is a local, evidence-separated
walkthrough. Do not imply that it is a hosted service or one continuous live
checkout repair. Keep the browser on loopback and hide credential-bearing
terminals and environment files.

## 0:00–0:20 — The problem

“A retry in checkout can charge the same order twice. ReplayForge is designed
to help an engineer investigate and repair that incident, while keeping the
model's suggestion separate from evidence that a change actually works.”

Show the ReplayForge workbench title and the incident / source-authorization
surface. If using a test screenshot, say it is captured from synthetic local
browser acceptance.

## 0:20–0:50 — Durable local workflow

“The run API records an incident and durable status. Repository consent is
scoped to a pinned commit and approved paths. In today's disposable local
acceptance, the API creates a run, reads persisted events, cancels, and reloads
it. That test does not execute repository code.”

Show the local run/status browser capture or the corresponding passing cases in
`docs/evidence/m5-final-api-workbench-20261029.xml`.

## 0:50–1:20 — Separate historical fixture record

“This retained report is from a separate fixed checkout fixture run. It records
remote baseline, candidate and clean-replay operations and names NVIDIA
Nemotron through Nebius Token Factory. It is historical evidence for that
fixture, not today's generalized production workflow and not a current signed
seven-gate verdict.”

Show `docs/evidence/m2-checkout-e2e/report.json`; avoid splicing that run into
the current synthetic acceptance.

## 1:20–1:55 — What the verifier proves locally

“The four-phase verification test stores baseline, candidate, existing-suite
and clean-replay records in disposable PostgreSQL. Seven gates pass for
synthetic test identities, and the persisted signed envelope is independently
checked. This demonstrates verifier behavior, not a production signature or a
live checkout repair.”

Show `docs/evidence/m3-azure-es256-v5-durable-acceptance-final.xml` and point to
the `positive_v5` test result. Keep the `synthetic/test-only` label visible.

## 1:55–2:20 — Honest evaluation status

Open `http://127.0.0.1:3000/evaluation` from the local web app. Say: “One
fixture is available, three are unavailable, and zero have been attempted. We
have no measured benchmark rate.” Show the unauthenticated evidence label and
the mobile layout only if the recording can do so without exposing unrelated
desktop content.

## 2:20–2:45 — Limitations and close

“The runtime image import hit a provider pull 404; the derived pytest image and
qualified sandbox are not ready. The current checkout has not completed the
four trusted live phases, and M3 and M4 remain open. No GitHub PR was created.
We report what the local and historical evidence shows, and leave unsupported
claims out.”

## Recording checklist

- Keep the final edit between 2:30 and 2:55, comfortably below three minutes.
- Record the application on a desktop browser and show key modules in action;
  use only retained screenshots when a local API is not running.
- Upload to YouTube and set public visibility only after owner review.
- Use English narration, captions if practical, and no copyrighted music or
  third-party marks without permission.
- Read `docs/submission/known-limitations.md` before recording and keep the
  synthetic, historical and live labels explicit on screen.
- Do not show `.env`, PEM files, private trust documents or raw provider logs.
