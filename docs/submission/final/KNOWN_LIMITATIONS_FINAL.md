# Known limitations — final (verified 2026-10-30)

Say these plainly in the video, Devpost text and any Q&A.

1. **No live end-to-end checkout repair is demonstrated.** The deployed workbench reached GitHub login, repository selection, consent and investigation intake. The investigation (`56f56757`) was observed stalled at CREATED with zero events (operator screenshot). Unless Codex supplies new retained evidence, do not claim source collection, diagnosis or repair on the deployed app.
2. **M3 and M4 remain open.** No qualified live four-phase run, no trusted live seven-gate verdict, no production `VERIFIED` checkout result, no real PR created.
3. **Seven-gate and v5 results are synthetic.** 64 disposable-PostgreSQL tests and 49 unit tests (Azure ES256 v5), plus the 280-test M4 security suite, establish local behavior with test identities. Production Azure signing/trust is not provisioned.
4. **The historical fixture report is a different run.** `docs/evidence/m2-checkout-e2e/report.json` (2026-10-19, `FIXTURE_REPAIR_PASSED`) used a fixed synthetic checkout fixture at commit `64acd340…`, not the pinned demo commit `0ab2adbf…`. Its own limitations: not a VERIFIED verdict, provider UUID is not a verified OCI digest, standalone path rather than the durable worker graph.
5. **Nebius sandbox image import failed** (accepted, then registry pull HTTP 404). No qualified sandbox runtime on a derived pytest image.
6. **Nemotron:** a fixed synthetic Token Factory probe (2026-10-15, HTTP 200, 22 in / 27 out tokens) and the fixture run naming `nvidia/nemotron-3-super-120b-a12b`. No new inference was run for this submission.
7. **Evaluation:** 1 fixture available, 3 unavailable, 0 attempts, 0 live attempts. No benchmark success rate exists. Do not quote one.
8. The **static evaluation site** (`agreeable-flower-0846a9300.4.azurestaticapps.net`) is a read-only aggregate, not the product.
9. **Third-party licenses** (dependencies, NVIDIA Nemotron Open Model License) are separate from Apache-2.0; a full SBOM review is outstanding.
10. The checkout POC repo must not receive an automatic PR.
