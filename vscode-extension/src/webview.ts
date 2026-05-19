import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { Finding } from './reportParser';
import { resolveWorkspacePath } from './pathUtils';

// ── Nonce helper ──────────────────────────────────────────────────────────────

function getNonce(): string {
  let t = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    t += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return t;
}

// ── Inline SVG icon set ───────────────────────────────────────────────────────

const ICONS: Record<string, string> = {
  shield: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M8 1L2 3.6V8c0 3.3 2.6 6 6 7 3.4-1 6-3.7 6-7V3.6L8 1zm0 1.7 5 2.1V8c0 2.6-2 4.7-5 5.7C5 12.7 3 10.6 3 8V4.8l5-2.1z"/>
  </svg>`,
  warning: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M7.56 1h.88L15 13.5v.5H1v-.5L7.56 1zM8 3.4 3.14 13h9.72L8 3.4zM8.5 10v1h-1v-1h1zm0-4v3h-1V6h1z"/>
  </svg>`,
  bug: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M13.5 6H12V5a4 4 0 0 0-8 0v1H2.5v1H4v1H2.5v1H4v1H2.5c.4 1.8 2 3.2 3.8 3.5L7 14h2l-.3-1.5c1.8-.3 3.4-1.7 3.8-3.5H11V9h1.5V8H11V7h1.5V6zM6 5a2 2 0 0 1 4 0H6zm3 6H7V9h2v2zm0-3H7V6h2v2z"/>
  </svg>`,
  tools: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M14.1 1.9a.5.5 0 0 0-.7 0L9.5 5.8 8.3 4.6l3.8-3.8a.5.5 0 0 0-.7-.7L7.6 3.9 6.1 2.4a3.5 3.5 0 0 0-4.5 4.5l1-1 .7.7-2 2A3.5 3.5 0 0 0 5.7 13l2-2 .7.7-1 1a3.5 3.5 0 0 0 4.5-4.5L10.5 9.7 9.8 9l4.3-4.3a.5.5 0 0 0 0-.7l-1.4-1.4a.5.5 0 0 0-.7 0l-.7.7-.7-.7.7-.7zM4 11 8 7l1 1-4 4-1-1z"/>
  </svg>`,
  terminal: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M1 2h14v12H1V2zm1 1v10h12V3H2zm2 6.5 3-3-.7-.7L3 8.5l.7.7L5.6 11H7l-2-1.5zM8 11h4v-1H8v1z"/>
  </svg>`,
  check: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M2 8l4 4 8-8-1.4-1.4L6 9.2 3.4 6.6 2 8z"/>
  </svg>`,
  error: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 1a6 6 0 1 1 0 12A6 6 0 0 1 8 2zm-.5 3v5h1V5h-1zm0 6v1h1v-1h-1z"/>
  </svg>`,
  sparkle: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M8 1 9.5 6.5 15 8l-5.5 1.5L8 15l-1.5-5.5L1 8l5.5-1.5L8 1z"/>
  </svg>`,
  copy: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M4 4h8v1H4V4zM4 2h10v10h-1V3H4V2zM2 5h9v9H2V5zm1 1v7h7V6H3z"/>
  </svg>`,
  file: `<svg class="ico" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M9 1H3v14h10V5L9 1zm0 1.5 2.5 2.5H9V2.5zM4 14V2h4v4h4v8H4z"/>
  </svg>`,
};

function ico(name: string): string {
  return ICONS[name] ?? '';
}

// ── HTML escape ───────────────────────────────────────────────────────────────

function esc(s: string | null | undefined): string {
  return (s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Severity helpers ──────────────────────────────────────────────────────────

function sevClass(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical': return 'critical';
    case 'high':     return 'high';
    case 'medium':   return 'medium';
    default:         return 'info';
  }
}

// ── Card wrapper ──────────────────────────────────────────────────────────────

function card(header: string, body: string): string {
  return `<div class="card">
  <div class="card-header">${header}</div>
  <div class="card-body">${body}</div>
</div>`;
}

// ── Evidence chip normaliser ──────────────────────────────────────────────────

function toChipLabel(e: string): string {
  const s = e.toLowerCase();
  if (s.startsWith('visibility=')) {
    return s.replace('visibility=', '');
  }
  if (s.includes('payload') || s.includes('calldata')) { return 'payload'; }
  if (s.includes('abi.decode') || (s.includes('decode') && s.includes('param'))) { return 'abi.decode'; }
  if (s.includes('mint'))     { return 'token.mint'; }
  if (s.includes('transfer')) { return 'token.transfer'; }
  if (s.includes('withdraw')) { return 'withdraw'; }
  if (s.startsWith('confirmed:') || (s.includes('confirmed') && s.includes('no'))) {
    return '✓ confirmed: no guard';
  }
  if (s.includes('no access-control') || s.includes('no guard')) { return 'no guard'; }
  if (s.includes('cross') && s.includes('chain'))                { return 'cross-chain'; }
  // Strip regex noise and truncate
  return e.replace(/\\[a-zA-Z()\[\]{}*+?]/g, '').replace(/[\\()\[\]{}*+?]/g, '').trim().slice(0, 30);
}

// ── Section builders ──────────────────────────────────────────────────────────

function buildHero(f: Finding): string {
  const filename  = path.basename(f.file);
  const cls       = sevClass(f.severity);
  const category  = f.category.replace(/_/g, ' ');
  const summary   = esc(f.trust_assumption.attack_vector);

  return `<div class="hero hero-${cls}">
  <div class="hero-meta">
    ${ico('file')}
    <span class="hero-file">${esc(filename)}:${f.line}</span>
    <span class="hero-sep">·</span>
    <span class="hero-cat">${esc(category)}</span>
  </div>
  <div class="hero-title">
    ${ico('shield')}
    <span class="hero-fn" data-action="openFile">${esc(f.function)}</span>
    <span class="badge badge-${cls}">${esc(f.severity.toUpperCase())}</span>
  </div>
  <p class="hero-summary">${summary}</p>
</div>`;
}

function buildEvidence(f: Finding): string {
  const chips = (f.evidence ?? [])
    .map(e => `<span class="chip">${esc(toChipLabel(e))}</span>`)
    .join('');

  const scores = f.scores ? `
<div class="score-row">
  ${scoreChip('Exposure', f.scores.exposure, false)}
  ${scoreChip('Payload',  f.scores.payload,  false)}
  ${scoreChip('Mutation', f.scores.mutation, false)}
  ${scoreChip('Guard',    f.scores.guard,    true)}
</div>` : '';

  return card(
    `${ico('warning')} Evidence`,
    `<div class="chips">${chips}</div>${scores}`
  );
}

function scoreChip(label: string, val: boolean, invertAlert: boolean): string {
  const isAlert = val ? !invertAlert : invertAlert;
  const cls = isAlert ? 'score-alert' : val ? 'score-pass' : 'score-miss';
  const icon = val ? '✓' : '✗';
  return `<span class="score-chip ${cls}">${icon} ${label}</span>`;
}

function buildTrustAssumption(f: Finding): string {
  const ta = f.trust_assumption;

  const rows: [string, string][] = [
    ['Assumed Caller',     ta.assumed_trusted_caller],
    ['Unsafe Trust Path',  ta.attack_vector],
    ...(ta.implicit_assumption  ? [['Implicit Assumption',  ta.implicit_assumption]  as [string, string]] : []),
    ...(ta.missing_enforcement  ? [['Missing Enforcement',  ta.missing_enforcement]  as [string, string]] : []),
  ];

  const tableRows = rows.map(([k, v]) =>
    `<tr><td class="tbl-key">${esc(k)}</td><td class="tbl-val">${esc(v)}</td></tr>`
  ).join('');

  return card(
    `${ico('shield')} Trust Assumption`,
    `<table class="kv-table"><tbody>${tableRows}</tbody></table>`
  );
}

function buildAiAnalysis(f: Finding): string {
  if (!f.ai_analysis) { return ''; }
  const ai        = f.ai_analysis;
  const isSuccess = ai.llm_status === 'gemini_success';

  const statusCell = isSuccess
    ? `<span class="status-badge status-success">${ico('check')} AI Analysis</span>`
    : `<span class="status-badge status-fallback">${ico('warning')} Fallback</span>`;

  const providerCell = ai.ai_provider
    ? `${esc(ai.ai_provider)} / <code>${esc(ai.gemini_model ?? '')}</code>`
    : null;

  const rows: [string, string][] = [
    ['Status', statusCell],
    ...(providerCell ? [['Provider', providerCell] as [string, string]] : []),
    ...(isSuccess && ai.confidence != null
      ? [['Confidence', `${Math.round(ai.confidence * 100)}%`] as [string, string]]
      : []),
    ...(!isSuccess
      ? [['Message', `<span class="fallback-msg">${esc(ai.llm_display_message ?? 'Deterministic fallback used.')}</span>`] as [string, string]]
      : []),
  ];

  const tableRows = rows.map(([k, v]) =>
    `<tr><td class="tbl-key">${esc(k)}</td><td class="tbl-val">${v}</td></tr>`
  ).join('');

  const reasoning = isSuccess && ai.reasoning_summary
    ? `<p class="reasoning">${esc(ai.reasoning_summary)}</p>`
    : '';

  return card(
    `${ico('sparkle')} AI Analysis`,
    `<table class="kv-table"><tbody>${tableRows}</tbody></table>${reasoning}`
  );
}

function buildFoundry(f: Finding): string {
  if (!f.foundry) { return ''; }
  const fr = f.foundry;

  const bannerCls  = fr.passed === true  ? 'forge-pass'
                   : fr.passed === false ? 'forge-fail'
                   : 'forge-skip';
  const bannerIco  = fr.passed === true  ? ico('check')
                   : fr.passed === false ? ico('error')
                   : ico('warning');
  const bannerText = fr.passed === true  ? 'PASS'
                   : fr.passed === false ? 'FAIL'
                   : 'NOT RUN';

  const testFile   = fr.test_path ? path.basename(fr.test_path) : '';
  const banner     = `<div class="forge-banner ${bannerCls}">
  ${bannerIco}
  ${bannerText}${testFile ? ` <span style="font-weight:400;opacity:0.75;">— ${esc(testFile)}</span>` : ''}
</div>`;

  const output = fr.output_summary
    ? `<pre class="forge-out">${esc(fr.output_summary)}</pre>`
    : '';

  return card(`${ico('terminal')} Foundry Result`, `${banner}${output}`);
}

function buildPatch(f: Finding): string {
  if (!f.patch) { return ''; }

  return card(
    `${ico('tools')} Recommended Patch`,
    `<p class="patch-desc">${esc(f.patch.description)}</p>
<div class="code-wrap">
  <button class="copy-btn" data-action="copyPatch">${ico('copy')} Copy</button>
  <pre class="code-block">${esc(f.patch.code_snippet)}</pre>
</div>`
  );
}

function buildActions(f: Finding): string {
  const proofTestBtn = f.proofTestPath
    ? `<button class="btn-secondary" data-action="openProofTest">${ico('bug')} Open Proof Test</button>`
    : '';

  return `<div class="actions">
  <button class="btn-primary" data-action="openFile">${ico('file')} Go to Source</button>
  ${proofTestBtn}
</div>`;
}

function buildEmptyState(): string {
  return `<div class="empty-state">
  <svg class="empty-icon" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
    <path fill="currentColor" d="M20 2L4 9v10C4 28.4 11.2 35.8 20 38c8.8-2.2 16-9.6 16-19V9L20 2zm0 4.2 13 5.8V19c0 7.6-5.5 14-13 16.3C12.5 33 7 26.6 7 19V12l13-5.8zm-1.5 9.3v9h3v-9h-3zm0 11v3h3v-3h-3z"/>
  </svg>
  <div class="empty-title">No trust-boundary issues detected</div>
  <div class="empty-sub">TrustGraph found no unguarded external entry points in this scan.</div>
</div>`;
}

// ── Panel class ───────────────────────────────────────────────────────────────

export class FindingDetailPanel {
  private static current?: FindingDetailPanel;
  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private finding: Finding | null;

  private constructor(panel: vscode.WebviewPanel, finding: Finding | null, extensionUri: vscode.Uri) {
    this.panel        = panel;
    this.finding      = finding;
    this.extensionUri = extensionUri;
    this.panel.onDidDispose(() => { FindingDetailPanel.current = undefined; });
    this.panel.webview.onDidReceiveMessage(this.handleMessage.bind(this));
    this.render();
  }

  static show(finding: Finding, extensionUri: vscode.Uri): void {
    if (FindingDetailPanel.current) {
      FindingDetailPanel.current.update(finding);
      FindingDetailPanel.current.panel.reveal(vscode.ViewColumn.Two);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      'trustgraphFinding',
      `TrustGraph: ${finding.function}`,
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'media')],
      }
    );

    FindingDetailPanel.current = new FindingDetailPanel(panel, finding, extensionUri);
  }

  static showEmpty(extensionUri: vscode.Uri): void {
    if (FindingDetailPanel.current) {
      FindingDetailPanel.current.update(null);
      FindingDetailPanel.current.panel.reveal(vscode.ViewColumn.Two);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      'trustgraphFinding',
      'TrustGraph: No Findings',
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'media')],
      }
    );

    FindingDetailPanel.current = new FindingDetailPanel(panel, null, extensionUri);
  }

  private update(finding: Finding | null): void {
    this.finding   = finding;
    this.panel.title = finding ? `TrustGraph: ${finding.function}` : 'TrustGraph: No Findings';
    this.render();
  }

  private handleMessage(msg: { command: string }): void {
    if (!this.finding) { return; }
    const f    = this.finding;
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

    const resolve = (p: string | null | undefined) =>
      root ? (resolveWorkspacePath(root, p) ?? p ?? '') : (p ?? '');

    if (msg.command === 'copyPatch' && f.patch) {
      vscode.env.clipboard.writeText(f.patch.code_snippet);
      vscode.window.showInformationMessage('Patch copied to clipboard.');

    } else if (msg.command === 'openProofTest' && f.proofTestPath) {
      const filePath = resolve(f.proofTestPath);
      if (!filePath || !fs.existsSync(filePath)) {
        vscode.window.showWarningMessage(`TrustGraph: Proof test file not found — ${filePath}`);
        return;
      }
      vscode.window.showTextDocument(vscode.Uri.file(filePath));

    } else if (msg.command === 'openFile') {
      const filePath = resolve(f.file);
      if (!filePath || !fs.existsSync(filePath)) {
        vscode.window.showWarningMessage(`TrustGraph: Source file not found — ${filePath}`);
        return;
      }
      const line = Math.max(0, (f.line ?? 1) - 1);
      vscode.window.showTextDocument(
        vscode.Uri.file(filePath),
        { selection: new vscode.Range(line, 0, line, 0) }
      );
    }
  }

  private render(): void {
    this.panel.webview.html = this.buildHtml();
  }

  private buildHtml(): string {
    const webview  = this.panel.webview;
    const nonce    = getNonce();
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, 'media', 'style.css')
    );
    const csp = `default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';`;

    const body = this.finding
      ? `
  ${buildHero(this.finding)}
  ${buildEvidence(this.finding)}
  ${buildTrustAssumption(this.finding)}
  ${buildAiAnalysis(this.finding)}
  ${buildFoundry(this.finding)}
  ${buildPatch(this.finding)}
  ${buildActions(this.finding)}`
      : buildEmptyState();

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <title>TrustGraph Finding</title>
  <link rel="stylesheet" href="${styleUri}">
</head>
<body>
  ${body}
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.addEventListener('click', function(e) {
      var el = e.target && e.target.closest('[data-action]');
      if (!el) { return; }
      vscode.postMessage({ command: el.dataset.action });
    });
  </script>
</body>
</html>`;
  }
}
