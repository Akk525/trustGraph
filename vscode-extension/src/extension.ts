import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { parseReport, Finding } from './reportParser';
import { applyDiagnostics, clearDiagnostics } from './diagnostics';
import { FindingsProvider, FindingItem } from './findingsProvider';
import { FindingDetailPanel } from './webview';
import { buildOptions, runAudit } from './trustgraphRunner';
import { resolveWorkspacePath } from './pathUtils';

let diagnosticCollection: vscode.DiagnosticCollection;
let findingsProvider: FindingsProvider;
let outputChannel: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext): void {
  diagnosticCollection = vscode.languages.createDiagnosticCollection('trustgraph');
  outputChannel        = vscode.window.createOutputChannel('TrustGraph');
  findingsProvider     = new FindingsProvider();

  const treeView = vscode.window.createTreeView('trustgraph.findings', {
    treeDataProvider: findingsProvider,
    showCollapseAll:  true,
  });

  context.subscriptions.push(
    diagnosticCollection,
    outputChannel,
    treeView,
    vscode.commands.registerCommand('trustgraph.runAudit',        runAuditCommand(context)),
    vscode.commands.registerCommand('trustgraph.openFindings',    openFindingsCommand(context)),
    vscode.commands.registerCommand('trustgraph.goToSource',      goToSourceCommand),
    vscode.commands.registerCommand('trustgraph.copyPatch',       copyPatchCommand),
    vscode.commands.registerCommand('trustgraph.openReport',      openReportCommand),
    vscode.commands.registerCommand('trustgraph.openExploitTest', openExploitTestCommand),
    vscode.commands.registerCommand('trustgraph.clearFindings',   clearFindingsCommand),
  );
}

export function deactivate(): void {
  diagnosticCollection?.clear();
}

// ── Argument unwrapping ───────────────────────────────────────────────────────
//
// VS Code passes the TreeItem to context-menu / inline-button commands, not the
// embedded Finding. This helper handles all three shapes that can arrive:
//   • FindingItem  → has a .finding property
//   • Finding      → has a .file property (called programmatically)
//   • undefined    → user invoked command without selection

function unwrapFinding(arg: unknown): Finding | undefined {
  if (!arg || typeof arg !== 'object') { return undefined; }
  // FindingItem wraps a Finding in .finding
  const maybeItem = arg as FindingItem;
  if (maybeItem.finding) { return maybeItem.finding; }
  // Plain Finding (called programmatically or passed via arguments array)
  const maybeFinding = arg as Finding;
  if (maybeFinding.file || maybeFinding.function || maybeFinding.exploit_path) {
    return maybeFinding;
  }
  return undefined;
}

function log(msg: string): void {
  outputChannel.appendLine(`[TrustGraph] ${msg}`);
}

// ── Path helpers ──────────────────────────────────────────────────────────────

function workspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

// Resolve + validate a path. Logs clearly on failure; returns undefined if bad.
function resolveAndCheck(
  rawPath: string | null | undefined,
  label: string,
): string | undefined {
  const root     = workspaceRoot();
  const resolved = root
    ? resolveWorkspacePath(root, rawPath) ?? rawPath ?? undefined
    : rawPath ?? undefined;

  if (!resolved) {
    log(`${label}: no path provided`);
    vscode.window.showWarningMessage(`TrustGraph: No ${label.toLowerCase()} associated with this finding.`);
    return undefined;
  }

  if (!fs.existsSync(resolved)) {
    log(`${label} not found: ${resolved}`);
    vscode.window.showWarningMessage(`TrustGraph: ${label} not found — ${resolved}`);
    return undefined;
  }

  return resolved;
}

// Resolve file/exploit_path to absolute paths at report-load time.
function resolveFindingPaths(f: Finding, root: string): Finding {
  return {
    ...f,
    file:         resolveWorkspacePath(root, f.file)         ?? f.file,
    exploit_path: resolveWorkspacePath(root, f.exploit_path) ?? f.exploit_path,
  };
}

// ── Commands ──────────────────────────────────────────────────────────────────

function runAuditCommand(context: vscode.ExtensionContext) {
  return async () => {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders?.length) {
      vscode.window.showErrorMessage('TrustGraph: Open a workspace folder first.');
      return;
    }

    const root = workspaceFolders[0].uri.fsPath;
    const opts = buildOptions(root);

    await vscode.window.withProgress(
      {
        location:    vscode.ProgressLocation.Notification,
        title:       'TrustGraph: Running audit…',
        cancellable: false,
      },
      async (progress) => {
        progress.report({ message: `Scanning ${path.basename(opts.contractPath)}` });

        const result = await runAudit(opts, outputChannel);

        if (!result.success || !result.reportJsonPath) {
          vscode.window.showErrorMessage(
            'TrustGraph: Audit failed or report.json not found. Check the Output channel.'
          );
          return;
        }

        const json    = fs.readFileSync(result.reportJsonPath, 'utf-8');
        const report  = parseReport(json);

        // Resolve all paths to absolute before handing to any consumer.
        const findings = (report.findings ?? []).map(f => resolveFindingPaths(f, root));

        findingsProvider.setFindings(findings);
        applyDiagnostics(diagnosticCollection, findings);

        const critical = findings.filter(f => f.severity === 'Critical').length;
        const medium   = findings.filter(f => f.severity === 'Medium').length;

        log(`Audit complete: ${critical} Critical, ${medium} Medium (${findings.length} total)`);
        vscode.window.showInformationMessage(
          `TrustGraph: ${critical} Critical, ${medium} Medium (${findings.length} total).`
        );
      }
    );
  };
}

// Handles both direct click (arguments: [Finding]) and context menu (FindingItem).
function openFindingsCommand(context: vscode.ExtensionContext) {
  return (arg?: unknown) => {
    const finding = unwrapFinding(arg);
    log(`openFindings: finding=${finding?.function ?? 'none'}, file=${finding?.file ?? 'none'}`);

    if (!finding) {
      vscode.window.showInformationMessage('TrustGraph: Click a finding in the sidebar to see details.');
      return;
    }
    FindingDetailPanel.show(finding, context.extensionUri);
  };
}

// Triggered by: inline ▶ button, context menu "Go to Source"
// VS Code passes the FindingItem; unwrapFinding extracts the embedded Finding.
function goToSourceCommand(arg?: unknown): void {
  const finding = unwrapFinding(arg);
  log(`goToSource: finding=${finding?.function ?? 'none'}, file=${finding?.file ?? 'none'}`);

  const filePath = resolveAndCheck(finding?.file, 'Source file');
  if (!filePath) { return; }

  const uri = vscode.Uri.file(filePath);
  const pos = new vscode.Position(Math.max(0, (finding!.line ?? 1) - 1), 0);
  vscode.window.showTextDocument(uri, { selection: new vscode.Range(pos, pos) });
}

// Triggered by: context menu "Copy Patch"
function copyPatchCommand(arg?: unknown): void {
  const finding = unwrapFinding(arg);
  log(`copyPatch: finding=${finding?.function ?? 'none'}, hasPatch=${Boolean(finding?.patch)}`);

  if (!finding?.patch?.code_snippet) {
    vscode.window.showWarningMessage('TrustGraph: No patch available for this finding.');
    return;
  }
  vscode.env.clipboard.writeText(finding.patch.code_snippet).then(() => {
    vscode.window.showInformationMessage('TrustGraph: Patch copied to clipboard.');
  });
}

function openReportCommand(): void {
  const root = workspaceRoot();
  if (!root) { return; }

  const cfg          = vscode.workspace.getConfiguration('trustgraph');
  const rawOutputDir = cfg.get<string>('outputDir', '.trustgraph');
  const outputDir    = resolveWorkspacePath(root, rawOutputDir) ?? path.join(root, '.trustgraph');

  // Prefer report.md; fall back to report.json.
  const mdPath   = path.join(outputDir, 'report.md');
  const jsonPath = path.join(outputDir, 'report.json');

  if (fs.existsSync(mdPath)) {
    vscode.window.showTextDocument(vscode.Uri.file(mdPath));
  } else if (fs.existsSync(jsonPath)) {
    vscode.window.showTextDocument(vscode.Uri.file(jsonPath));
  } else {
    vscode.window.showWarningMessage(
      `TrustGraph: No report found in ${outputDir}. Run an audit first.`
    );
  }
}

// Triggered by: inline ⚗ button, context menu "Open Exploit Test"
// Also accepts a plain string path for backwards compat.
function openExploitTestCommand(arg?: unknown): void {
  // Handle direct string path (legacy)
  if (typeof arg === 'string') {
    const filePath = resolveAndCheck(arg, 'Exploit test file');
    if (filePath) { vscode.window.showTextDocument(vscode.Uri.file(filePath)); }
    return;
  }

  const finding = unwrapFinding(arg);
  log(`openExploitTest: finding=${finding?.function ?? 'none'}, exploit_path=${finding?.exploit_path ?? 'none'}`);

  const filePath = resolveAndCheck(finding?.exploit_path, 'Exploit test file');
  if (!filePath) { return; }

  vscode.window.showTextDocument(vscode.Uri.file(filePath));
}

function clearFindingsCommand(): void {
  findingsProvider.clear();
  clearDiagnostics(diagnosticCollection);
  log('Findings cleared.');
  vscode.window.showInformationMessage('TrustGraph: Findings cleared.');
}
