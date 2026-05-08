import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import { resolveWorkspacePath } from './pathUtils';

export interface RunOptions {
  workspaceRoot: string;
  contractPath: string;
  outputDir: string;
  runFoundry: boolean;
  generateTest: boolean;
  reportFormat: string;
  cliPath: string;
}

export interface RunResult {
  success: boolean;
  reportJsonPath?: string;
  stdout: string;
  stderr: string;
}

export function buildOptions(workspaceRoot: string): RunOptions {
  const cfg            = vscode.workspace.getConfiguration('trustgraph');
  const rawContract    = cfg.get<string>('contractPath', '');
  const rawOutputDir   = cfg.get<string>('outputDir', '.trustgraph');

  return {
    workspaceRoot,
    contractPath: resolveWorkspacePath(workspaceRoot, rawContract) ?? workspaceRoot,
    outputDir:    resolveWorkspacePath(workspaceRoot, rawOutputDir) ?? path.join(workspaceRoot, '.trustgraph'),
    runFoundry:   cfg.get<boolean>('runFoundry', false),
    generateTest: cfg.get<boolean>('generateTest', true),
    reportFormat: cfg.get<string>('reportFormat', 'both'),
    cliPath:      cfg.get<string>('cliPath', ''),
  };
}

export async function runAudit(
  opts: RunOptions,
  outputChannel: vscode.OutputChannel
): Promise<RunResult> {
  const cli = opts.cliPath || 'trustgraph';

  const args = [
    'audit',
    opts.contractPath,
    opts.generateTest ? '--generate-test' : '--no-generate-test',
    opts.runFoundry ? '--run-foundry' : '--no-run-foundry',
    '--report-format', opts.reportFormat,
    '--output-dir', opts.outputDir,
  ];

  outputChannel.appendLine(`\n[TrustGraph] cwd: ${opts.workspaceRoot}`);
  outputChannel.appendLine(`[TrustGraph] Running: ${cli} ${args.join(' ')}`);
  outputChannel.show(true);

  return new Promise(resolve => {
    const proc = cp.spawn(cli, args, {
      shell: true,
      env:   process.env,
      cwd:   opts.workspaceRoot,
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      stdout += text;
      outputChannel.append(text);
    });

    proc.stderr.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      outputChannel.append(text);
    });

    proc.on('close', (code) => {
      const reportJsonPath = path.join(opts.outputDir, 'report.json');
      const success        = code === 0 && fs.existsSync(reportJsonPath);

      if (code !== 0) {
        outputChannel.appendLine(`\n[TrustGraph] Process exited with code ${code}`);
      } else {
        outputChannel.appendLine(`\n[TrustGraph] Done. Output: ${opts.outputDir}`);
      }

      resolve({
        success,
        reportJsonPath: fs.existsSync(reportJsonPath) ? reportJsonPath : undefined,
        stdout,
        stderr,
      });
    });

    proc.on('error', (err) => {
      outputChannel.appendLine(`\n[TrustGraph] Failed to start process: ${err.message}`);
      resolve({ success: false, stdout, stderr: stderr + err.message });
    });
  });
}
