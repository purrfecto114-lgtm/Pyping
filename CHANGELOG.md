# Changelog

## v0.4.0

### Maintenance rewrite (version unchanged)

- Rewrote the visual shell while preserving the parameter/action/statistics/output/status layout order.
- Replaced mixed native-theme rendering with one controlled Fluent-inspired light/dark palette.
- Replaced label-frame interiors with explicit surface sections, eliminating mismatched label and container backgrounds.
- Standardized entry fonts, vertical padding, requested heights and column widths.
- Added adaptive startup sizing based on the usable desktop work area instead of DPI-multiplying a fixed geometry.
- Added a compact-height layout for small displays and guaranteed that the output log remains visible at startup.
- Reduced the statistics area to one responsive row so the log receives more vertical space.
- Rewrote the Windows build driver with Python 3.10+ discovery, checked native exit codes and explicit build modes.
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
