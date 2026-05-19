import * as vscode from 'vscode';
import * as path from 'path';
import { Finding } from './reportParser';

// ── State ─────────────────────────────────────────────────────────────────────

type ProviderState = 'idle' | 'clean' | 'findings';

// ── Severity helpers ──────────────────────────────────────────────────────────

function sevIconName(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical': return 'bug';
    case 'high':     return 'bug';
    case 'medium':   return 'warning';
    default:         return 'info';
  }
}

function sevColor(severity: string): vscode.ThemeColor {
  switch (severity.toLowerCase()) {
    case 'critical': return new vscode.ThemeColor('testing.iconFailed');
    case 'high':     return new vscode.ThemeColor('editorWarning.foreground');
    case 'medium':   return new vscode.ThemeColor('testing.iconQueued');
    default:         return new vscode.ThemeColor('editorInfo.foreground');
  }
}

function groupIconName(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical': return 'error';
    case 'high':     return 'warning';
    case 'medium':   return 'warning';
    default:         return 'info';
  }
}

// contextValue encodes available actions so when-clauses in menus can branch
function findingContextValue(finding: Finding): string {
  const parts = ['trustgraphFinding'];
  if (finding.proofTestPath) { parts.push('proofTest'); }
  if (finding.patch)        { parts.push('patch'); }
  return parts.join(':');
}

function buildTooltip(finding: Finding): vscode.MarkdownString {
  const filename = path.basename(finding.file);
  const md       = new vscode.MarkdownString();
  md.isTrusted   = true;
  md.appendMarkdown(`**Severity:** ${finding.severity}  \n`);
  md.appendMarkdown(`**Function:** \`${finding.function}\`  \n`);
  md.appendMarkdown(`**File:** ${filename}:${finding.line}  \n`);
  md.appendMarkdown(`**Category:** ${finding.category}  \n\n`);
  md.appendMarkdown(`**Unsafe Trust Path:** ${finding.trust_assumption.attack_vector}`);
  return md;
}

// ── Tree items ────────────────────────────────────────────────────────────────

export class FindingItem extends vscode.TreeItem {
  constructor(
    label: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
    public readonly finding?: Finding,
    public readonly severityGroup?: string,
  ) {
    super(label, collapsibleState);

    if (finding) {
      const filename    = path.basename(finding.file);
      this.description  = `${filename}:${finding.line}`;
      this.tooltip      = buildTooltip(finding);
      this.contextValue = findingContextValue(finding);
      this.iconPath     = new vscode.ThemeIcon(sevIconName(finding.severity), sevColor(finding.severity));
      this.command      = {
        command:   'trustgraph.openFindings',
        title:     'Open Detail',
        arguments: [finding],
      };

    } else if (severityGroup) {
      this.iconPath     = new vscode.ThemeIcon(groupIconName(severityGroup), sevColor(severityGroup));
      this.contextValue = 'trustgraphSeverityGroup';
    }
  }
}

// ── Tree data provider ────────────────────────────────────────────────────────

export class FindingsProvider implements vscode.TreeDataProvider<FindingItem> {
  private readonly _onDidChangeTreeData =
    new vscode.EventEmitter<FindingItem | undefined | null>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private findings: Finding[]    = [];
  private state:    ProviderState = 'idle';

  setFindings(findings: Finding[]): void {
    this.findings = findings;
    this.state    = findings.length > 0 ? 'findings' : 'clean';
    this._onDidChangeTreeData.fire(null);
  }

  clear(): void {
    this.findings = [];
    this.state    = 'idle';
    this._onDidChangeTreeData.fire(null);
  }

  getTreeItem(element: FindingItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: FindingItem): FindingItem[] {
    if (!element) {
      return this.buildRoot();
    }
    if (element.severityGroup) {
      return this.findings
        .filter(f => f.severity === element.severityGroup)
        .map(f => new FindingItem(f.function, vscode.TreeItemCollapsibleState.None, f));
    }
    return [];
  }

  private buildRoot(): FindingItem[] {
    switch (this.state) {
      case 'idle':     return this.buildIdle();
      case 'clean':    return this.buildClean();
      case 'findings': return this.buildFindings();
    }
  }

  private buildIdle(): FindingItem[] {
    const item        = new FindingItem('Not run yet', vscode.TreeItemCollapsibleState.None);
    item.description  = 'Press ▶ to run an audit';
    item.tooltip      = 'Click the play button above or run "TrustGraph: Run Audit" from the Command Palette.';
    item.iconPath     = new vscode.ThemeIcon('circle-outline');
    item.contextValue = 'trustgraphIdle';
    return [item];
  }

  private buildClean(): FindingItem[] {
    const item        = new FindingItem('No findings', vscode.TreeItemCollapsibleState.None);
    item.description  = 'No trust-boundary issues detected';
    item.tooltip      = 'TrustGraph found no unguarded trust-boundary violations.';
    item.iconPath     = new vscode.ThemeIcon('pass-filled', new vscode.ThemeColor('testing.iconPassed'));
    item.contextValue = 'trustgraphClean';
    return [item];
  }

  private buildFindings(): FindingItem[] {
    const order   = ['Critical', 'High', 'Medium', 'Informational'];
    const counts  = Object.fromEntries(
      order.map(sev => [sev, this.findings.filter(f => f.severity === sev).length])
    );
    const total   = this.findings.length;

    // Summary row
    const summaryParts = order
      .filter(sev => counts[sev] > 0)
      .map(sev => `${counts[sev]} ${sev}`);

    const summary         = new FindingItem('Scan Summary', vscode.TreeItemCollapsibleState.None);
    summary.description   = `${summaryParts.join(' · ')} · ${total} Total`;
    summary.iconPath      = new vscode.ThemeIcon('shield');
    summary.contextValue  = 'trustgraphSummary';
    summary.tooltip       = `Last scan: ${total} finding${total !== 1 ? 's' : ''} across ${summaryParts.join(', ')}.`;

    const rows: FindingItem[] = [summary];

    for (const sev of order) {
      const count = counts[sev];
      if (count === 0) { continue; }

      const group         = new FindingItem(sev, vscode.TreeItemCollapsibleState.Expanded, undefined, sev);
      group.description   = `${count} finding${count !== 1 ? 's' : ''}`;
      rows.push(group);
    }

    return rows;
  }
}
