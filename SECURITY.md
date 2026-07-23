# Security policy

## Supported version

Security fixes are applied to the current `0.4.x` source line.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, or sensitive logs. Use GitHub's private vulnerability reporting feature for this repository when available. Include the affected version, reproduction steps, impact, and the smallest safe proof of concept.

## Release security model

- Pull-request and validation workflows use a read-only `GITHUB_TOKEN`.
- The Windows build job has `contents: read` only and cannot publish releases.
- The publish job receives `contents: write`, but it does not check out or execute repository code; it only verifies and uploads prebuilt artifacts.
- Third-party Actions are pinned to immutable full commit SHAs.
- Release tags must reference commits reachable from the repository default branch.
- Release assets are constrained by an exact manifest and SHA-256 checksums.
- The `release` GitHub Environment should be configured with protected tags and required reviewers for production repositories.

## Local build trust

Build scripts create an isolated `.venv-build` and install the complete Windows dependency set from `packaging/requirements-windows.lock` with `--require-hashes`. The lock contains reviewed SHA-256 hashes for supported Python 3.10–3.13 x64 wheels. Build dependencies are force-reinstalled and the resulting site-packages set is checked against an allowlist derived from the lock. Review dependency and hash updates before merging them. Windows binaries must be produced on a trusted Windows runner.
