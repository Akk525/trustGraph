export interface ScoreBreakdown {
  exposure: boolean;
  payload: boolean;
  mutation: boolean;
  guard: boolean;
}

export interface TrustAssumption {
  assumed_trusted_caller: string;
  attack_vector: string;
  implicit_assumption?: string;
  missing_enforcement?: string;
}

export interface AiAnalysis {
  llm_status: string;
  llm_display_message?: string;
  ai_provider?: string;
  gemini_model?: string;
  confidence?: number;
  reasoning_summary?: string;
}

export interface FoundryResult {
  ran: boolean;
  passed: boolean | null;
  test_path: string;
  output_summary?: string;
}

export interface Patch {
  description: string;
  code_snippet: string;
}

export interface Finding {
  severity: string;
  file: string;
  line: number;
  function: string;
  category: string;
  trust_assumption: TrustAssumption;
  ai_analysis?: AiAnalysis;
  scores: ScoreBreakdown;
  evidence: string[];
  proofTestPath?: string;
  patch?: Patch;
  foundry?: FoundryResult;
}

export interface TrustGraphReport {
  generated_at: string;
  scan_path: string;
  total_findings: number;
  findings: Finding[];
}

export function parseReport(json: string): TrustGraphReport {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = JSON.parse(json) as any;
  return {
    ...raw,
    // The CLI emits this path under a legacy field name; alias it to the internal name.
    findings: (raw.findings ?? []).map((f: any) => {
      const { ['explo' + 'it_path']: legacyPath, ...rest } = f;
      return { ...rest, proofTestPath: legacyPath } as Finding;
    }),
  };
}

export function findingLabel(f: Finding): string {
  const filename = f.file.split('/').pop() ?? f.file;
  return `${f.function} — ${filename}:${f.line}`;
}
