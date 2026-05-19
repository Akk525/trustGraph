# Changelog

All notable changes to the TrustGraph VS Code extension are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2025-05-19

Initial public release.

### Added

- **Run Audit command** — invokes the TrustGraph CLI and streams output to a dedicated Output channel
- **Inline diagnostics** — Critical findings as errors (red squiggles), Medium findings as warnings (yellow squiggles) in `.sol` files
- **Findings sidebar** — tree view grouped by severity with scan summary header
- **Finding detail webview** — shows evidence, trust assumption, optional Gemini explanation, Foundry CI result, and patch template
- **Go to Source** — jumps to the flagged line in the source file
- **Open Proof Test** — opens the generated `.t.sol` Foundry proof test in the editor
- **Copy Patch** — copies the recommended patch snippet to clipboard
- **Open Report File** — opens `report.md` or `report.json` from the output directory
- **Clear Findings** — removes all diagnostics and resets the sidebar
- **Configuration** — `trustgraph.*` settings for CLI path, contract path, output directory, report format, and Foundry execution
- **Progress notification** — displays scan progress while the CLI runs
- **Output channel** — full CLI stdout/stderr accessible via `View → Output → TrustGraph`

---

## Versioning Guide

- **Patch** (`0.1.x`) — bug fixes, packaging corrections, documentation updates
- **Minor** (`0.x.0`) — new commands, new configuration options, UI changes, new detection capabilities in the CLI
- **Major** (`x.0.0`) — breaking changes to extension API, major architecture changes, removal of features
