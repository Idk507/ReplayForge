# Authorized live read smoke

The user authorized read testing against https://github.com/Idk507/maf_ds_agent.
On 2026-10-14, its main branch was resolved once to
08d828835fcb0cada16aca45c721ad0075e0c70e. The smoke uses that exact commit and
README.md only; it cannot accept an arbitrary repository or execute source.

Offline default (does not read credentials or use the network):

```powershell
uv run python -m scripts.check_github
```

Explicit live CLI-credential mode:

```powershell
uv run python -m scripts.check_github --live --use-gh-cli --commit 08d828835fcb0cada16aca45c721ad0075e0c70e
```

Without --use-gh-cli, the live command accepts REPLAYFORGE_GITHUB_READ_TOKEN from
the process environment. Do not paste credentials into chat or commit them. CLI
retrieval uses shell-free gh auth token with captured output and timeout, entirely
in memory. Invalid live commit arguments are rejected before credential access.
Repository access metadata reported admin/push capabilities. The token's exact
least-privilege scope was not established; this smoke performs GETs only.

The first live attempt failed PATH_DENIED on excluded .env.example metadata.
An offline regression proved that content admission and portable metadata syntax
were conflated. Excluded regular secret filenames are now omitted without blob
fetch; allowed_entry still denies their content, and traversal, reserved-name,
symlink and submodule checks remain. No secret filename was added to the allowlist.

After the fix, the real adapter returned PASSED with REMOTE_SERVICE origin,
root b70f64691e5ffc8a79286233fafe07c9e9f03460 and README SHA256
32673d68690246993d5990be48db183279fb1c4f6d29c2492717944a5e30c737
(15629 bytes). Source bytes and credentials were not retained. Safe failed and
passed observations are separately saved in docs/evidence/m1-github-live-read.json
and m1-github-live-read-fixed.json.

This proves live authenticated resolution and a scoped file fetch/hash through
ReplayForge. It does not prove a read-only installation token, durable snapshot
storage, full live API/worker orchestration, signed authorship, repair verification
or sandbox execution. No GitHub writes or paid provider calls occurred.
