TrustGraph

TrustGraph is a trust-boundary vulnerability analysis workflow for Solidity smart contracts.

It detects externally callable functions that implicitly trust unverified callers, generates executable Foundry exploit proofs, and surfaces findings through a VS Code investigation workflow.

TrustGraph is deterministic-first: heuristic analysis is the sole source of truth for vulnerability severity. Optional Gemini 2.5 Flash reasoning enriches trust-assumption explanations but never overrides deterministic findings.

⸻

Features

* Deterministic-first trust-boundary analysis
* Cross-chain receiver vulnerability detection
* Auto-generated Foundry exploit PoCs
* Optional Gemini-powered semantic reasoning
* Markdown + JSON report generation
* VS Code diagnostics and investigation workflow
* GitHub Actions CI/CD integration

⸻

Install

git clone https://github.com/your-org/trustgraph
cd trustgraph
pip install -e .

Optional Gemini reasoning

pip install -e ".[gemini]"
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.5-flash"

Without a Gemini API key, TrustGraph automatically falls back to deterministic-only analysis.

You can also store environment variables in a local .env file:

GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash

.env is ignored by git by default.

⸻

Usage

Basic audit

trustgraph audit path/to/contracts/src

Generate exploit PoC tests

trustgraph audit path/to/src --generate-test

Execute generated Foundry tests

trustgraph audit path/to/src \
  --generate-test \
  --run-foundry

Generate Markdown + JSON reports

trustgraph audit path/to/src \
  --report-format both

Deterministic-only mode

trustgraph audit path/to/src --no-ai

⸻

VS Code Extension

TrustGraph ships with a local VS Code extension for interactive investigation workflows.

Features include:

* inline diagnostics
* findings sidebar
* exploit test navigation
* patch copying
* Foundry result rendering
* trust-assumption inspection

Run locally

cd vscode-extension
npm install
npm run compile

Then:

1. Open vscode-extension/ in VS Code
2. Press F5
3. Open the main TrustGraph repo in the Extension Development Host
4. Run:

TrustGraph: Run Audit

Recommended extension settings

{
  "trustgraph.cliPath": "/path/to/trustgraph",
  "trustgraph.contractPath": "examples/vulnerable-crosschain/src",
  "trustgraph.outputDir": ".trustgraph",
  "trustgraph.runFoundry": true
}

Generated reports and exploit tests are written into:

.trustgraph/

⸻

Demo

# Install forge-std once
cd examples/vulnerable-crosschain
forge install foundry-rs/forge-std --no-commit
cd ../..
# Run TrustGraph
trustgraph audit examples/vulnerable-crosschain/src \
  --generate-test \
  --run-foundry \
  --report-format both

Expected result:

Critical → receiveMessage
Medium   → mint

Generated outputs:

.trustgraph/
├── report.md
├── report.json
└── tests/

⸻

Why CrossCurve-style bugs matter

CrossCurve-style exploits occur when contracts implicitly trust an external bridge/router caller without enforcing that assumption on-chain.

An attacker can directly invoke the receiver with forged payloads, bypassing the bridge entirely and triggering privileged state changes such as token minting or withdrawals.

TrustGraph focuses specifically on this trust-boundary failure pattern.

⸻

Vulnerability Predicate

Vulnerable(f) = E(f) AND P(f) AND V(f) AND NOT G(f)

Predicate	Meaning
E(f)	Function is external or public
P(f)	Accepts attacker-controlled payloads
V(f)	Performs critical state mutation
G(f)	Enforces caller validation or access control

Severity

Condition	Severity
E + P + V + no guard	Critical
E + V + no guard	Medium
Otherwise	Informational

⸻

Architecture

VS Code Extension
        ↓
TrustGraph CLI
        ↓
LangGraph workflow
        ↓
Deterministic analysis
        ↓
Optional Gemini reasoning
        ↓
Exploit generation
        ↓
Foundry validation

Project structure

trustgraph/
├── cli.py
├── graph.py
├── models.py
├── scanners/
├── agents/
├── runners/
└── reports/

LangGraph workflow

load_contracts
    ↓
static_scan
    ↓
infer_trust_assumptions
    ↓
validate_guards
    ↓
generate_exploit_tests
    ↓
run_foundry_tests
    ↓
recommend_patches
    ↓
generate_report

⸻

Deterministic-first design

TrustGraph does not rely on LLMs for vulnerability decisions.

The deterministic scanner is the sole source of truth for:

* exposure detection
* payload detection
* critical state mutation detection
* access-control validation
* severity assignment

Gemini is used only for:

* semantic trust-assumption explanation
* auditor-readable reasoning
* natural-language summaries

If the AI layer fails or is disabled, TrustGraph still:

* detects findings
* generates exploit PoCs
* runs Foundry proofs
* produces remediation guidance

Gemini fallback behaviour

Condition	Behaviour
--no-ai	deterministic only
missing API key	deterministic fallback
Gemini API failure	deterministic fallback
invalid Gemini response	deterministic fallback
Gemini success	enriched reasoning

⸻

CI/CD

TrustGraph includes a GitHub Actions workflow that:

1. installs TrustGraph + Foundry
2. audits the example contracts
3. asserts a Critical finding exists
4. uploads reports as artifacts

This enables merge-gating for unguarded receiver patterns.

⸻

Limitations

* Heuristic-based matching may miss custom patterns or produce false positives.
* No cross-function taint tracking.
* No inheritance-aware modifier resolution.
* Multi-file data flow is not fully traced.

⸻

Future Work

* AST-level parsing via solc --ast-compact-json
* Project-level call graph analysis
* Cross-function taint tracking
* Slither integration for complementary analysis
* Vulnerability-class-specific exploit templates
* Confidence-gated AI explanations and auditor guidance
* Semgrep rule export

⸻

Security Disclaimer

TrustGraph is an experimental research prototype and should not be treated as a substitute for professional smart contract audits.

AI-generated explanations are informational only and should not be treated as ground truth. All findings and exploit proofs require human review.

⸻

Acknowledgements

Inspired by the August 2024 CrossCurve bridge exploit and related trust-boundary failures in bridge receiver architectures.