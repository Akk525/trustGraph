import * as vscode from 'vscode';
import { Finding } from './reportParser';

export function applyDiagnostics(
  collection: vscode.DiagnosticCollection,
  findings: Finding[]
): void {
  collection.clear();

  const byFile = new Map<string, vscode.Diagnostic[]>();

  for (const finding of findings) {
    const severity =
      finding.severity === 'Critical'
        ? vscode.DiagnosticSeverity.Error
        : finding.severity === 'Medium'
        ? vscode.DiagnosticSeverity.Warning
        : vscode.DiagnosticSeverity.Information;

    const line = Math.max(0, (finding.line ?? 1) - 1);
    const range = new vscode.Range(line, 0, line, 999);
    const message = `[TrustGraph ${finding.severity}] ${finding.function}: ${finding.trust_assumption.attack_vector}`;
    const diag = new vscode.Diagnostic(range, message, severity);
    diag.source = 'TrustGraph';
    diag.code = finding.category;

    const existing = byFile.get(finding.file) ?? [];
    existing.push(diag);
    byFile.set(finding.file, existing);
  }

  for (const [filePath, diags] of byFile) {
    const uri = vscode.Uri.file(filePath);
    collection.set(uri, diags);
  }
}

export function clearDiagnostics(collection: vscode.DiagnosticCollection): void {
  collection.clear();
}
