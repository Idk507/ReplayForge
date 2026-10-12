# Hackathon Submission Plan and Narrative

## Submission promise
'Agent-generated fixes are easy to suggest. ReplayForge proves them.' ReplayForge analyzes a software incident, builds a behavioral regression test, proves the bug exists on the original commit, proposes a constrained patch using NVIDIA Nemotron via Nebius Token Factory, and reruns the incident from a clean sandbox. It packages evidence and asks a human to approve the exact patch before opening a GitHub pull request.

## Official verification
Deadline Fri 30 Oct 2026 10:00 PDT (22:30 IST); judging Dec 1–15. Submit under Coding and Agentic Engineering. Must use NVIDIA open source model and Nebius Token Factory runtime inference or Nebius AI Cloud compute; include working demo URL, public code repo + OSI license + setup README, description, <3-minute public YouTube functionality video and feedback about technologies used. Review exact current rules: https://nebiusglobalaihackathon.devpost.com/rules . Review resources: https://nebiusglobalaihackathon.devpost.com/resources . Submission at least one day early strongly recommended.

## Storyboard (2:55 target)
0:00–0:30 show broken checkout duplicating charge. 0:30–0:55 submit incident. 0:55–1:30 expose Nemotron investigation, original test reproduction and Tavily documentation evidence if used. 1:30–2:15 show patch, regression tests, clean replay, explicit gates. 2:15–2:40 show negative/inconclusive gate or tamper rejection, then human approval and PR. 2:40–2:55 show architecture and platform credits, honest measured metric. Do not use fake live output; cut narration rather than accelerate beyond comprehension.

## Devpost text fields
Project title: ReplayForge — Verified Autonomous Incident Repair. One-liner: 'From failing incident to evidence-backed GitHub pull request, powered by NVIDIA Nemotron on Nebius.' Inspiration: engineering waste and false confidence of code suggestions. What it does: ingest -> reproduce -> patch -> clean replay -> evidence -> approve -> PR. How built: FastAPI/LangGraph, Nemotron Token Factory, isolation backend, Tavily, Next.js, OTel, GitHub integration. Challenges: valid reproduction, sandbox safety, flaky tests, evidence provenance. Accomplishments: fill from ACTUAL measurements. Learned: evidence-based trust and strict cost/security boundaries. What's next: enterprise observability connectors and languages. Include clearly labeled limitations and exact live feature set.

## Audit checklist
Confirm anonymous browser can open project demo safely; repo LICENSE visibly present and source/build/run instructions work; live demo uses Nebius at runtime; NVIDIA model identity visible via safe telemetry; Tavily bonus usage only claimed if real runtime call; YouTube public and <3 min; correct hackathon track selected; all Devpost required forms complete; links resolve; secrets scanned; account credits sufficient during Dec 1–15; judging demo does not require unapproved production credentials. Keep submission confirmation evidence.
