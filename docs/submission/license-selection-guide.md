# Repository license selection — owner action

GitHub's public repository metadata was read-only checked on 2026-10-29. The
repo is public, but the default branch has no detectable license and fetching
`LICENSE` returned 404. Devpost's rules require a public repository with an
open-source license visible on the repository page.

No license has been added. The repository owner must select one after checking
that they have the right to license all project contributions and that bundled
third-party material keeps its own notices.

## Recommendation for owner review

**Apache-2.0** is the strongest fit to review for this software project: it is
permissive and includes an express patent license, which is useful when others
reuse and modify an agent/workbench codebase. It carries more notice and
redistribution conditions than MIT. This is a recommendation, not a grant of
rights or a legal conclusion. The owner must confirm that they have authority
to license every contribution and any copied fixture material.

The unmodified official text is in root `LICENSE.proposed`. That filename is
deliberately not `LICENSE`; GitHub should not treat it as the repository's
selected license. Do not rename it until the owner approves Apache-2.0 and
confirms the copyright holder/notice.

The Python lock includes dependencies under MIT, BSD, Apache-2.0,
Apache-2.0-or-BSD (`cryptography`), and LGPL-3.0 (`psycopg`). The npm lock
includes MIT, Apache-2.0, BSD, and platform-specific `sharp`/libvips packages
under LGPL-3.0-or-later or compound licenses. The application repository does
not vendor `node_modules` or Python environments, so these remain separate
dependency licenses; a binary/container distribution still needs a complete
third-party notice and redistribution review. `uv.lock` does not itself carry
complete license texts, and this was not a full SBOM scan.

The NVIDIA Nemotron model and the separate checkout fixture are not covered by
ReplayForge's project license. Preserve their own license and ownership terms.

## Other common choices

- **MIT:** short permissive terms; requires keeping the copyright and license
  notice with copies or substantial portions.
- **Apache-2.0:** permissive terms with an express patent license and additional
  notice requirements.
- **GPL-3.0:** copyleft terms for redistributed derivative works.
- **AGPL-3.0:** GPL-style copyleft that also addresses modified software made
  available over a network.

These are practical distinctions, not legal advice. Do not choose based only
on the hackathon requirement. Before publishing:

1. Decide the license and confirm contributor/IP authority.
2. Add the exact, unmodified license text in root `LICENSE` and any required
   copyright line.
3. Check source and dependency notices for separate licenses; run a complete
   license/SBOM review before distributing a binary or container.
4. Commit and publish the reviewed source revision only after a secret/private-
   evidence review.
5. Confirm GitHub detects the license and displays it in the repository's About
   area; verify the license file on the exact public revision.
