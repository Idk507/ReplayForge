# License status and third-party scope

## Owner-approved project license

On 2026-10-29, project owner Dhanushkumar R. approved Apache License 2.0 for
original ReplayForge source code, subject to applicable third-party obligations.
Copyright year: 2026. The root `LICENSE` contains the official, unmodified
Apache-2.0 text. It was compared byte-for-byte with the official Apache
`LICENSE-2.0.txt`; SHA-256:
`CFC7749B96F63BD31C3C42B5C471BF756814053E847C10F3EB003417BC523D30`.
The root `NOTICE` records the attribution and clarifies that third-party
materials retain their own terms. `README.md` and Python package metadata
identify Apache-2.0.

This grant covers only original ReplayForge source and project-authored
materials. It does not relicense the checkout fixture, dependencies, NVIDIA
model weights, provider services, or other third-party materials.

## Third-party obligations and remaining review

The targeted dependency metadata review found Python dependencies under MIT,
BSD, Apache-2.0, Apache-2.0-or-BSD (`cryptography`), and LGPL-3.0 (`psycopg`).
The npm lock includes MIT, Apache-2.0, BSD, and platform-specific `sharp`/
libvips packages under LGPL-3.0-or-later or compound licenses. Dependencies are
not vendored in this source release candidate. `uv.lock` and npm lockfiles do
not contain all license texts; no complete SBOM/license scan has been done.
Before distributing a binary or container, prepare the full third-party notices
and review each applicable redistribution obligation. Preserve upstream notices
when redistributing dependency artifacts.

On 2026-10-29, the owner-authorized release was pushed to public `main`.
GitHub's repository license endpoint reports root `LICENSE` with SPDX
identifier `Apache-2.0`: https://github.com/Idk507/ReplayForge/blob/main/LICENSE.
The license commit is `ce9f8cfa1ea2c8c04d460876ed923c9e95e24eed`, following
release-candidate commit `7eff51d758aee783c1c527844d94849abaea8bb9`. The
latter is immutable and does not contain the license; its published follow-up
does.
