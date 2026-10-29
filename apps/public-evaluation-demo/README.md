# Public evaluation demo (static artifact)

This folder contains a static, read-only evaluation page for Azure Static Web Apps. It is intentionally isolated from the Next.js app and FastAPI API: the deployment root is `site/`, which contains only HTML, CSS, JavaScript, a sanitized aggregate JSON snapshot, and security headers. It includes no backend, API routes, database configuration, credentials, signing material, or provider calls.

The report is the same file as `apps/web/public/evidence/m4-evaluation-status.json`. Refresh it with:

```powershell
Copy-Item apps/web/public/evidence/m4-evaluation-status.json apps/public-evaluation-demo/site/evidence/m4-evaluation-status.json
```

The data is explicitly `UNAUTHENTICATED_AGGREGATE`; it does not prove executions or authorize gates. Deploy `site/` as the app artifact and leave the API location empty. The page is a static snapshot, not the interactive repair workbench.