# TrustGraph

**Deterministic trust-boundary vulnerability analysis for Solidity smart contracts.**

TrustGraph detects externally callable functions that implicitly trust unverified callers, generates executable Foundry exploit proofs, and surfaces findings through a VS Code investigation workflow.

Detection is **deterministic-first** — heuristic analysis is the sole source of truth for severity. Optional Gemini 2.5 Flash reasoning enriches trust-assumption explanations but never overrides deterministic findings.

> Experimental research prototype. Not a substitute for professional smart contract audits.

---

## Features

- Deterministic trust-boundary analysis (no LLM required for detection)
- Cross-chain receiver vulnerability detection
- Auto-generated Foundry exploit PoCs, executed by `forge test`
- Optional Gemini-powered semantic reasoning
- Markdown + JSON report generation
- VS Code investigation workflow with diagnostics, findings navigation, exploit validation, and remediation guidance
- GitHub Actions CI/CD integration

---

## Install

```bash
git clone https://github.com/your-org/trustgraph
cd trustgraph
pip install -e .
```

**With optional Gemini reasoning:**

```bash
pip install -e ".[gemini]"
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.5-flash"   # optional, this is the default
```

Without a Gemini API key, TrustGraph falls back to deterministic-only analysis automatically. You can also store keys in a local `.env` file (gitignored by default):

```
GEMINI_API_KEY=your_key_here
```

---

## Usage

```bash
# Basic audit
trustgraph audit path/to/contracts/src

# Generate Foundry exploit PoC tests
trustgraph audit path/to/src --generate-test

# Generate tests + run forge test
trustgraph audit path/to/src --generate-test --run-foundry

# Markdown + JSON reports
trustgraph audit path/to/src --report-format both

# Deterministic-only mode (no LLM calls)
trustgraph audit path/to/src --no-ai
```

**All options:**

```
Arguments:
  PATH                    .sol file or directory  [required]

Options:
  --generate-test / --no-generate-test   Generate Foundry exploit PoC  [default: generate-test]
  --run-foundry / --no-run-foundry       Execute forge test             [default: no-run-foundry]
  --report-format TEXT                   markdown | json | both         [default: markdown]
  --output-dir TEXT                      Output directory               [default: trustgraph-output]
  --no-ai                                Disable LLM calls
```

---

## Demo

```bash
# Run the full pipeline against the bundled example
trustgraph audit examples/vulnerable-crosschain/src \
  --generate-test \
  --run-foundry \
  --report-format both \
  --output-dir .trustgraph
```

Expected terminal output:

```
Critical → receiveMessage   VulnerableReceiver.sol:27
Medium   → mint             MockToken.sol:18

[PASS] test_directInvocationExploit() (gas: 67996)
```

Generated outputs:

```
.trustgraph/
├── report.md
├── report.json
└── tests/
    └── VulnerableReceiverExploit.t.sol
```

### Demo Video

[Watch Demo Video](https://example.com/trustgraph-demo)

### Findings Sidebar

TrustGraph surfaces findings directly inside VS Code through a severity-grouped investigation sidebar.

![Findings Sidebar](screenshots/findings-sidebar.png)

### Critical Finding Investigation

Each finding expands into a full investigation panel containing deterministic evidence, trust assumptions, Gemini reasoning, remediation guidance, and executable Foundry validation results.

![Critical Finding Panel](screenshots/critical-finding-panel.png)

### Generated Exploit Proof

TrustGraph generates executable Foundry exploit proofs for vulnerable trust-boundary flows.

![Exploit Test](screenshots/exploit-test.png)

---

## VS Code Investigation Workflow

TrustGraph ships with a local VS Code extension for interactive investigation.

**Setup:**

```bash
cd vscode-extension
npm install
npm run compile
```

1. Open `vscode-extension/` in VS Code and press **F5** to launch the Extension Development Host.
2. Open the main TrustGraph repo in the new window.
3. Run `TrustGraph: Run Audit` from the Command Palette.

**Recommended `.vscode/settings.json`:**

```json
{
  "trustgraph.cliPath": "/path/to/trustgraph",
  "trustgraph.contractPath": "examples/vulnerable-crosschain/src",
  "trustgraph.outputDir": ".trustgraph",
  "trustgraph.runFoundry": true
}
```

Reports and exploit tests are written to `.trustgraph/` (gitignored).

**Extension features:**

| Feature | Description |
|---|---|
| Inline diagnostics | Red/yellow squiggles on vulnerable functions |
| Findings sidebar | Severity-grouped tree with scan summary |
| Detail webview | Evidence, trust assumption, AI analysis, Foundry result, patch |
| Go to Source | Jump to the exact finding line |
| Open Exploit Test | Open the generated `.t.sol` |
| Copy Patch | Copy the recommended fix to clipboard |

---

## Why CrossCurve-style bugs matter

In August 2024, the CrossCurve bridge was drained for ~$5M. The root cause: a cross-chain receiver function accepted arbitrary payloads from **any caller**, not just the trusted bridge endpoint. The attacker called it directly with a forged payload, bypassing the bridge entirely.

This pattern — an implicit trust assumption with no on-chain enforcement — recurs across LayerZero receivers, Axelar gateways, and custom bridge integrations.

---

## Vulnerability Predicate

```
Vulnerable(f) = E(f) ∧ P(f) ∧ V(f) ∧ ¬G(f)
```

| Predicate | Meaning | Detection |
|---|---|---|
| `E(f)` | `external` or `public` visibility | Regex on signature |
| `P(f)` | Accepts attacker-controlled payload | `bytes calldata`, `abi.decode`, `payload`/`data`/`message` params |
| `V(f)` | Critical state mutation | `.mint(`, `.transfer(`, `.withdraw(`, `balances[` |
| `G(f)` | Caller guard present | `require(msg.sender ==`, `onlyOwner`, `trustedBridge`, sig verification |

**Severity assignment:**

| Score | Severity |
|---|---|
| E + P + V + no guard | **Critical** |
| E + V + no guard | **Medium** |
| Otherwise | Informational |

---

## Architecture

```
VS Code Extension
      ↓  subprocess
TrustGraph CLI (Typer)
      ↓
LangGraph StateGraph
      ↓
load_contracts
      ↓
static_scan              ← E/P/V/G heuristic scoring
      ↓
infer_trust_assumptions  ← deterministic + optional Gemini
      ↓
validate_guards          ← confirm guard absence
      ↓
generate_exploit_tests   ← Foundry PoC templating
      ↓
run_foundry_tests        ← forge test subprocess
      ↓
recommend_patches        ← deterministic templates
      ↓
generate_report          ← JSON + Markdown
```

**Project layout:**

```
trustgraph/
├── cli.py              — Typer CLI entry point
├── graph.py            — LangGraph StateGraph (8 nodes)
├── models.py           — Pydantic models + WorkflowState
├── scanners/
│   └── solidity.py     — Regex function extractor + heuristic scorer
├── agents/
│   ├── trust_assumption.py   — Deterministic classifier + Gemini reasoning
│   ├── exploit_generator.py  — Foundry PoC template engine
│   └── patch_recommender.py  — Patch suggestion templates
├── runners/
│   └── foundry.py      — forge test subprocess runner
└── reports/
    ├── markdown.py     — Markdown report generator
    └── json_report.py  — JSON report generator
```

---

## Deterministic-first design

TrustGraph does not rely on LLMs for vulnerability decisions.

**The deterministic scanner is the sole source of truth for:**
- exposure detection (E)
- payload detection (P)
- critical state mutation detection (V)
- access-control / guard detection (G)
- severity assignment (Critical / Medium / Informational)

**Gemini is used only for:**
- semantic trust-assumption explanation
- auditor-readable natural-language reasoning

If the AI layer fails or is disabled, TrustGraph still detects findings, generates exploit PoCs, runs Foundry proofs, and produces remediation guidance.

**Gemini fallback behaviour:**

| Condition | Behaviour | `llm_status` |
|---|---|---|
| `--no-ai` passed | Deterministic only | `no_ai_forced` |
| No API key | Deterministic fallback | `missing_api_key_fallback` |
| Gemini API error / quota | Deterministic fallback | `gemini_error_fallback` |
| Invalid Gemini response | Deterministic fallback | `failed_parse_fallback` |
| Gemini success | Enriched reasoning | `gemini_success` |

---

## CI/CD

TrustGraph includes a GitHub Actions workflow (`.github/workflows/trustgraph.yml`) that:

1. Checks out the repo with submodules (forge-std)
2. Installs TrustGraph and Foundry
3. Runs the audit against the bundled example contracts
4. Asserts a Critical `receiveMessage` finding is detected
5. Uploads reports as build artifacts

---

## Limitations

- Heuristic keyword matching — may miss custom patterns or produce false positives on unusual code.
- No cross-function taint tracking — a guard in a calling function is not detected.
- No inheritance resolution — modifiers defined in a parent contract are not traced.
- Single-file scope — multi-file data flows are not traced.

---

## Future Work

- AST-level parsing via `solc --ast-compact-json`
- Cross-function and cross-contract call graph analysis
- Slither integration for complementary symbolic analysis
- Confidence-gated AI severity escalation with human confirmation
- Semgrep rule export from confirmed findings

---

## Security Disclaimer

TrustGraph is an experimental research prototype. It has not itself been security-audited.

AI-generated explanations are informational only. The Foundry exploit proof confirms exploitability of the generated test case — it does not guarantee the absence of other attack paths. All findings require human review before drawing production conclusions.

---

## Acknowledgements

Inspired by the August 2024 CrossCurve bridge exploit and the broader class of trust-boundary failures in cross-chain bridge receiver architectures.

## References

- Feist et al. *Slither: A Static Analysis Framework for Smart Contracts* (2019)
- Tsankov et al. *Securify: Practical Security Analysis of Smart Contracts* (2018)
- Tikhomirov et al. *SmartCheck: Static Analysis of Ethereum Smart Contracts* (2018)
- ConsenSys Diligence. *Mythril Classic*
- Semgrep: https://semgrep.dev