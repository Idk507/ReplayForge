# ReplayForge — Repository Structure and Module Ownership

```text
replayforge/
├── README.md
├── LICENSE
├── Architecture.md
├── AGENTS.md
├── project_scope.md
├── instructions.md
├── todo.md
├── system_design.md
├── repository_structure.md
├── milestones.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock                         # generated during implementation
├── apps/
│   ├── api/
│   │   └── replayforge_api/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── security.py
│   │       ├── dependencies.py
│   │       ├── api/v1/{runs,events,artifacts,approvals,pull_requests}.py
│   │       ├── domain/{models,state_machine,policies,errors}.py
│   │       ├── services/{run_service,approval_service,evidence_service}.py
│   │       ├── storage/{db,models,repository,outbox,leases}.py
│   │       └── migrations/
│   └── web/
│       ├── app/{page.tsx,runs/[id]/page.tsx,benchmarks/page.tsx}
│       ├── components/{run-timeline,incident-form,diff-viewer,gate-matrix,approval-dialog}.tsx
│       ├── lib/{api-client,auth,sse,contracts}.ts
│       └── tests/
├── packages/
│   ├── contracts/{run.py,evidence.py,tools.py,events.py,approval.py}
│   ├── policies/{path_policy.py,command_policy.py,budget.py,redaction.py}
│   ├── providers/{nebius.py,tavily.py,github.py}
│   └── telemetry/{tracing.py,metrics.py,logging.py}
├── services/
│   ├── orchestrator/{graph.py,state.py,nodes,checkpoints.py,worker.py}
│   └── runner/{interface.py,remote.py,fake.py,artifact_collector.py}
├── benchmarks/
│   ├── fixtures/{checkout,aggregation,concurrency}/
│   ├── oracles/                     # never loaded into agent context
│   ├── cases.jsonl
│   ├── harness.py
│   └── results/                    # observed run outputs only
├── infra/{deploy,terraform_or_iac,network,observability}/
├── scripts/{check_nebius.py,seed_fixtures.py,e2e_fixture.py,check_submission.py}
├── specs/{contracts.md,openapi.yaml,fixtures.md}
├── docs/{security.md,evaluation.md,ui.md,implementation-log.md,submission.md}
├── tests/{unit,contract,integration,security,e2e}/
└── .github/workflows/{ci.yml,dependency-audit.yml}
```

All files and folders in this tree are **planned target layout**, not implied created implementation. The documentation package creates only Markdown guidance and scaffold directories.

## Ownership mapping
`apps/api` owns HTTP auth, API transactions, durable records and privileged approval routes. `services/orchestrator` owns state transitions and stage routing but not HTTP authorization. `services/runner` owns isolated command execution and termination, never GitHub tokens. `packages/contracts` is shared Python schema source and generates OpenAPI/TypeScript contracts; manually duplicated schema definitions are prohibited. `packages/providers` owns external SDK adapters and mocks. `packages/policies` owns deterministic allowlists and risk limits. `benchmarks/oracles` is inaccessible to runtime agents. `apps/web` owns visualization and user interaction, not verdict calculation. `infra` owns separate worker identities and network policy.

## Naming and import guidance
Expose stable `Protocol` interfaces for providers and the runner. Prefer absolute imports and dependency injection. Avoid circular imports from API into packages. API handlers remain thin; domain invariants are centrally tested. Organize `nodes` as one file per distinct stage if the graph grows; avoid a monolithic thousand-line agent class. Every cross-boundary message has schema version. Use snake_case Python modules, explicit REST naming, and normalized artifact IDs.

## Developer branch strategy
`main` is protected; feature branches `feat/contracts`, `feat/control-plane`, `feat/nebius`, `feat/runner`, `feat/verification`, `feat/frontend`, `feat/github-pr`, `test/adversarial`. Independent coding assistants may use separate worktrees. Merge shared contracts before dependent module work. No concurrent edits to a central schema without review. CI remains green after each milestone. Feature flags: `ENABLE_TAVILY`, `ENABLE_PR_CREATION`, `ENABLE_PUBLIC_DEMO`, `SANDBOX_BACKEND`, `ALLOW_FIXTURE_ONLY`, default dangerous actions to disabled.
