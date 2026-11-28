# Changelog

## v0.5.1

### Fixes and footprint

- Failure-events dialog: the notebook, tabs and event tree now follow the
  light/dark palettes instead of the raw clam theme (fixed poor contrast);
  the empty state is rendered as a centered label instead of a filler row
  (fixed CJK text being clipped in half); the hint text is shorter with
  DPI-aware tree row heights.
- Frozen builds now exclude the unused Pillow format plugins and auxiliary
  backends (only PNG output is needed): the Linux onedir payload shrinks
  from 60 MiB to 50 MiB (pillow.libs 15 MiB -> 8.1 MiB); Windows payloads
  shrink accordingly. UPX stays disabled on purpose: packed executables are
  a common antivirus false-positive, which outweighs the size gain.

## v0.5.0

### Reliability and measurement correctness

- Skipped missed send slots instead of replaying them: a slow probe no longer
  triggers an immediate catch-up burst, and the schedule stays on its
  monotonic-clock grid.
- Stopping a session no longer drops the result of the probe that was already
  in flight; it is recorded as an explicit `cancelled` entry (result unknown)
  and excluded from failure-rate statistics instead of silently disappearing
  or being counted as a timeout.
- IPv6 link-local interface scopes (for example `fe80::1%7`) survive target
  resolution into the backend call.
- ping3 exceptions are now classified by exception type with the module-level
  EXCEPTIONS flag set once at adapter init, keeping ICMP type/code details and
  the responding source address in the record instead of compressing them to
  "returned False".

### System ping fallback

- Added a system-ping backend (Windows `ping.exe`, Linux iputils/BusyBox-style
  `ping`, macOS `ping`/`ping6`) used only when the ping3 backend cannot run at
  all (missing dependency, insufficient privileges). Ordinary timeouts and
  unreachables never trigger a backend switch, so sampling stays comparable.
- The switch happens once per session, is logged with its reason, and every
  real send is counted exactly once. Child processes use argument arrays with
  no shell and inherit a cleaned library search path under PyInstaller.
- Success requires real echo-reply evidence (failure text is checked before
  the exit code, because Windows `ping.exe` can exit 0 after total loss).
- The in-flight system ping process is cancelled promptly on stop.

### Failure events and the failure-event browser

- New incremental failure-event detector (single pass over the record stream,
  no rescans): consecutive failures merge into network events; resolver /
  permission / dependency errors become separate local-tool events and never
  masquerade as network failures.
- "Select chart time range" dialog gained a failure-events page; picking an
  event and a ±30 s / ±1 min / ±5 min window charts the period around the
  observed event span, clamped to the session bounds. Unrecovered and
  interrupted events are marked honestly (a stop is an observation boundary,
  not a recovery time).

### Chart queries and rendering

- Chart statistics and aggregation moved off the GUI thread onto a
  short-lived read-only WAL snapshot connection with session-identity guards,
  so million-row sessions no longer block the interface and stale queries can
  never populate a newer session.
- Aggregated buckets keep the success-avg latency plus a min/max envelope:
  mixed buckets no longer hide the latency curve (the 99.5%-success session
  now still shows latency), and pure-failure buckets still break the line.
- The time axis includes dates whenever the range spans midnight.

### Compatibility and toolchain

- The minimum Python version is now 3.11 everywhere (packaging metadata,
  build script discovery, docs); the 3.10 claim was dropped because the
  validation toolchain imports `tomllib`, which is 3.11+.
- CI now runs the GUI smoke test under Xvfb on Linux (and directly on
  Windows).  macOS CI was attempted but the account's GitHub plan does not
  currently support macOS runners (jobs fail with "configuration not
  supported"), so it is not included until that changes; macOS remains an
  untested-by-CI target.

## v0.4.0

### Security hardening update (version unchanged)

- Split Windows build and release publication into separate least-privilege jobs.
- Pinned every external GitHub Action to an immutable full commit SHA.
- Removed Chocolatey installation, shared dependency caching, wildcard release uploads and the third-party release Action.
- Added exact release manifests, SHA-256 verification, protected release output names and a read-only CI workflow.
- Added a complete SHA-256 hashed Windows dependency lock for Python 3.10–3.13, force-reinstallation, and an installed-distribution allowlist; unverified source distributions are disabled during frozen builds.
- Added safe project-bound cleanup that rejects external paths, never traverses reparse points, and removes stale `*.egg-info` metadata.
- Replaced the dangling vendored Inno Setup language-file reference with the compiler-provided Simplified Chinese language file.
- Changed SQLite writes from silent replacement to transactional duplicate rejection.
- Added atomic CSV, PNG and text exports, CSV formula neutralization and active-database overwrite protection.
- Required release tags to point to commits reachable from the default branch.
- Removed PATH-controlled font discovery, bounded PNG dimensions and chart sampling, limited stored error details, and cancelled pending Tk callbacks on chart close.
- Added `SECURITY.md`, Dependabot configuration and expanded security regression tests.

### Maintenance rewrite (version unchanged)

- Rewrote the visual shell while preserving the parameter/action/statistics/output/status layout order.
- Replaced mixed native-theme rendering with one controlled Fluent-inspired light/dark palette.
- Replaced label-frame interiors with explicit surface sections, eliminating mismatched label and container backgrounds.
- Standardized entry fonts, vertical padding, requested heights and column widths.
- Added adaptive startup sizing based on the usable desktop work area instead of DPI-multiplying a fixed geometry.
- Added a compact-height layout for small displays and guaranteed that the output log remains visible at startup.
- Reduced the statistics area to one responsive row so the log receives more vertical space.
- Rewrote the Windows build driver with Python 3.10–3.13 discovery, checked native exit codes and explicit build modes.
- Fixed batch exit-code propagation and Inno Setup paths that depended on the caller's working directory.
- Added a safe clean mode and removed generated caches and obsolete validation artifacts from release sources.
- Updated the Windows workflow and added packaging regression tests.

### GUI

- Preserved the existing parameters/actions/output/status layout while standardizing spacing, alignment and typography.
- Added responsive statistic cards and a live status bar.
- Added system, light and dark themes.
- Added keyboard shortcuts for start, stop, chart creation and CSV export.
- Added colored log categories, auto-scroll control, copy, clear and TXT export.

### Data and export

- Added streaming CSV export through an independent SQLite read connection.
- Added current-session data clearing with confirmation.
- Added a 24-hour chart range preset.
- Added application icons for source, wheel and frozen builds.

### Packaging

- Added reproducible PyInstaller onedir and onefile spec files.
- Added Windows PowerShell and batch build scripts.
- Added an Inno Setup installer definition using non-administrator installation by default.
- Added a Windows GitHub Actions build workflow and SHA256 generation.
- Added Windows version resources and packaging documentation.

### Validation

- Added CSV streaming tests and theme-switch GUI smoke coverage.
- Expanded static validation to check translation parity, version consistency and release files.


## v0.3.0

### Fixed

- Prevented `ping3` boolean `False` from being formatted as a successful zero-millisecond result.
- Rejected `NaN`, infinity, invalid integers, unsafe packet sizes and excessive ranges.
- Replaced mixed rolling/lifetime statistics with exact lifetime counters.
- Removed the unnecessary sleep after the final count-mode request.
- Switched elapsed-time control to `time.monotonic()` and interruptible `Event.wait()`.
- Added per-request sequence numbers.
- Replaced IPv4-only `gethostbyname()` with dual-stack `getaddrinfo()` resolution.
- Removed automatic chart creation after stop/completion.
- Added manual chart creation with selectable time ranges.
- Added SQLite-backed long-session storage and bounded UI queues.
- Added chart downsampling with exact range statistics.
- Broke latency lines at failed requests and separated timeout/other-error markers.
- Replaced screen-capture PNG export with deterministic Pillow rendering.
- Removed the old timed destructive unsaved-chart dialog.
- Preserved the real worker thread across language changes.
- Limited real-time log growth.
- Replaced deprecated locale fallback behavior.

### Added

- `pyproject.toml` and `requirements.txt`.
- Automated unit tests, GUI smoke test and static policy validator.
- Explicit per-ping timeout control.
- IPv4/IPv6 resolution details in the log.
