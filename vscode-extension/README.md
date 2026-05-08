# TrustGraph VS Code Extension

Inline diagnostics, sidebar findings tree, and detail webviews for [TrustGraph](../README.md) — the Solidity trust-boundary vulnerability scanner.

---

## Requirements

- TrustGraph CLI installed and accessible (see main README)
- VS Code 1.85+

---

## Install (local dev)

```bash
cd vscode-extension
npm install
npm run compile    # builds ./out/
```

Press **F5** in VS Code (with the extension folder open) to launch the Extension Development Host.

---

## Output directory

All generated files (reports, exploit tests) are written to a hidden `.trustgraph/` folder inside your workspace root by default:

```
<workspace>/
  .trustgraph/
    report.md
    report.json
    tests/
      VulnerableReceiverExploit.t.sol
```

This keeps your working tree clean. Add it to `.gitignore` to avoid committing generated outputs:

```
# .gitignore
.trustgraph/
```

To change the output location, set `trustgraph.outputDir` to any relative or absolute path.

---

## Configuration

All settings are under `trustgraph.*` in VS Code settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `trustgraph.contractPath` | workspace root | Path to `.sol` file or directory (relative or absolute) |
| `trustgraph.outputDir` | `.trustgraph` | Output directory for reports and tests (relative or absolute) |
| `trustgraph.generateTest` | `true` | Generate Foundry PoC exploit tests |
| `trustgraph.runFoundry` | `false` | Execute `forge test` after generating tests |
| `trustgraph.reportFormat` | `both` | `markdown`, `json`, or `both` |
| `trustgraph.cliPath` | *(PATH lookup)* | Full path to `trustgraph` binary |

Relative paths in `contractPath` and `outputDir` are resolved against the workspace root.

If the `trustgraph` binary is installed via pyenv or a virtualenv not in VS Code's PATH, set `trustgraph.cliPath`:

```json
"trustgraph.cliPath": "/Users/akk/.pyenv/versions/3.12.4/bin/trustgraph"
```

---

## Commands

| Command | Description |
|---------|-------------|
| `TrustGraph: Run Audit` | Run the CLI, parse `report.json`, populate diagnostics and tree |
| `Open Detail` *(sidebar context)* | Show finding detail webview |
| `Go to Source` *(sidebar context)* | Jump to the finding's source line |
| `Open Exploit Test` *(sidebar context)* | Open the generated `.t.sol` file |
| `Copy Patch` *(sidebar context)* | Copy the recommended patch to clipboard |
| `TrustGraph: Open Report File` | Open `report.md` (or `report.json`) from the output directory |
| `TrustGraph: Clear Findings` | Remove all diagnostics and reset the sidebar |

The **▶ Run Audit** and **Clear** buttons are also available in the TrustGraph sidebar toolbar.

---

## Demo Flow

1. Open the TrustGraph project (or any Solidity project) in VS Code.
2. Set `trustgraph.cliPath` if needed.
3. Run **TrustGraph: Run Audit** from the Command Palette (`⌘⇧P`).
4. A progress notification appears while the CLI runs; output streams to the **TrustGraph** Output channel.
5. When done:
   - Critical findings appear as **red squiggles** in `.sol` files.
   - Medium findings appear as **yellow squiggles**.
   - The **TrustGraph sidebar** (shield icon) shows a Scan Summary and findings grouped by severity.
6. Click any finding to open the **detail webview** — evidence, trust assumption, AI analysis, patch, and Foundry CI result.
7. Right-click a finding for the context menu: **Open Detail**, **Go to Source**, **Open Exploit Test**, **Copy Patch**.

---

## Troubleshooting

### "TrustGraph: Audit failed or report.json not found"

- Check the **TrustGraph** Output channel (`View → Output → TrustGraph`) for the full command and error.
- Make sure `trustgraph.cliPath` points to the correct binary: `which trustgraph` or `/Users/you/.pyenv/versions/3.12.4/bin/trustgraph`.
- Confirm `trustgraph.contractPath` is a valid `.sol` file or directory containing `.sol` files.

### "Source file not found" / "Exploit test file not found"

- Open the TrustGraph Output channel — the full resolved path is logged.
- If you moved the output directory after running the audit, run **TrustGraph: Run Audit** again to regenerate with the current `trustgraph.outputDir`.
- Relative paths are resolved against the workspace root. Use an absolute path in settings if in doubt.

### Gemini quota exceeded

The extension shows "Gemini quota exceeded; deterministic fallback used." in the AI Analysis card. This is expected behaviour — findings are still reported with full deterministic evidence. No action needed; the audit result is still valid.

### No Gemini API key

If `GEMINI_API_KEY` is not set in the shell that launched VS Code, the tool falls back to deterministic classification automatically. Set the key in your shell profile or in a `.env` file at the workspace root (it is gitignored by default).

### Foundry not installed

If `trustgraph.runFoundry` is `true` but `forge` is not on PATH, the Foundry step will fail silently and the finding will show "NOT RUN" in the Foundry Result card. Install Foundry via `curl -L https://foundry.paradigm.xyz | bash && foundryup`.

### Extension does not activate

The extension activates on `onLanguage:solidity`, `workspaceContains:**/*.sol`, or when the TrustGraph sidebar view is opened. If none of these fire, run **Developer: Reload Window** and click the shield icon in the Activity Bar.

---

## Architecture

```
src/
├── extension.ts        — activate(), command registrations
├── trustgraphRunner.ts — spawn CLI subprocess, resolve config
├── reportParser.ts     — TypeScript interfaces, JSON parsing
├── diagnostics.ts      — DiagnosticCollection management
├── findingsProvider.ts — TreeDataProvider (severity groups, state machine)
├── pathUtils.ts        — resolveWorkspacePath() utility
└── webview.ts          — FindingDetailPanel (WebviewPanel)
media/
├── style.css           — VS Code CSS variables theming
└── trustgraph-icon.svg — Activity bar icon
```
