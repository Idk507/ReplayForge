# Public demo deployment record

Deployed 2026-10-29 to Azure Static Web Apps Free as a static, read-only
evaluation snapshot.

- Public URL: https://agreeable-flower-0846a9300.4.azurestaticapps.net
- Azure resource: `replayforge-public-eval-dhanu-2026`
- Resource group: `replayforge-demo-rg` (Central India)
- Static Web Apps region: East Asia; SKU: Free
- Source revision: `7d03be6ed58dc16ad071ae19e218502df9ef67be`
- Deployment root: `apps/public-evaluation-demo/site/`
- Deployment method: Azure Static Web Apps CLI 2.1.0, production environment
- Backend/API location: none

The deployment includes only the isolated static artifact: HTML, CSS,
JavaScript, the sanitized evaluation JSON and static web app security-header
configuration. It does not include the Next.js workbench, API, database,
Azure/Nebius credentials, or signing/collector trust. There are no anonymous
privileged operations. This page is not evidence of a live checkout repair.

## Live checks

Checked 2026-10-29 against the public hostname:

| Request | Result |
|---|---|
| `GET /` | 200; title `Evaluation status · ReplayForge` |
| `GET /evidence/m4-evaluation-status.json` | 200; schema v2, `UNAUTHENTICATED_AGGREGATE` |
| Evaluation totals | 1 available case, 3 unavailable, 0 attempts, 0 live attempts; measured rates remain null |
| `GET /api/v1/runs` | 404 |
| CSP and response headers | CSP, HSTS, `X-Content-Type-Options`, and `Referrer-Policy` present |
| `GET /staticwebapp.config.json` | 404; configuration file is not served as public content |

The demo can be refreshed and redeployed from the repository root:

```powershell
Copy-Item apps/web/public/evidence/m4-evaluation-status.json apps/public-evaluation-demo/site/evidence/m4-evaluation-status.json
$env:SWA_CLI_DEPLOYMENT_TOKEN = '<retrieve securely from the Azure Static Web App; never print or commit it>'
swa deploy apps/public-evaluation-demo/site --env production --app-name replayforge-public-eval-dhanu-2026 --resource-group replayforge-demo-rg
Remove-Item Env:SWA_CLI_DEPLOYMENT_TOKEN
```

The deployment token is a secret. Do not paste it into a shell transcript,
source file, issue, or evidence report. Azure resource creation used the Free
SKU; no paid compute or custom domain was configured. Confirm current account
and subscription terms before changing the hosting configuration.

## Remaining submission items

- Record and publish the reviewed video under three minutes.
- Complete the owner-authored Devpost feedback and submission fields.
- Verify the video and submission links after publication.

The static site is the public test-build URL; it is not the interactive
workbench. M3 remains incomplete because there is no qualified live checkout
execution with seven trusted live gates.
