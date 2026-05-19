# TrustGraph

> **Deterministic Solidity trust-boundary analysis with Foundry exploit proof generation.**

<!-- Recommended GitHub repo description: "Deterministic Solidity trust-boundary analysis with executable Foundry exploit validation." -->

TrustGraph scans Solidity contracts for externally callable functions that accept attacker-controlled payloads without caller guards, generates Foundry PoC tests for each finding, and runs them via `forge test`. A VS Code extension exposes findings as inline diagnostics with an investigation sidebar.

**Detection is deterministic**: an explicit four-predicate heuristic (E ∧ P ∧ V ∧ ¬G) determines every finding and severity. Optional Gemini 2.5 Flash adds natural-language explanation only — it cannot create, modify, or suppress findings.

---

## Quick Demo

```bash
trustgraph audit examples/vulnerable-crosschain/src \
  --generate-test \
  --run-foundry \
  --report-format both \
  --output-dir .trustgraph
```

Terminal output:

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

### Findings Sidebar

![Findings Sidebar](screenshots/findings-sidebar.png)

### Critical Finding Panel

Each finding panel shows deterministic evidence, access-control verdict, optional Gemini explanation, the generated exploit test, and a patch template.

![Critical Finding Panel](screenshots/critical-finding-panel.png)

### Generated Exploit PoC

![Exploit Test](screenshots/exploit-test.png)

---

## Why TrustGraph Exists

Cross-chain bridge receiver functions are a recurring attack surface: an `external` function accepts an arbitrary payload with no `msg.sender` check, and any caller can trigger privileged state changes directly. The August 2024 CrossCurve bridge exploit (~$5M) is a concrete example; the same pattern appears in LayerZero receivers, Axelar gateways, and custom bridge integrations.

TrustGraph addresses this with three concrete guarantees:

- **Auditable findings** — every result traces to a fixed predicate (E ∧ P ∧ V ∧ ¬G) applied to function source text. No model confidence scores, no sampling variance.
- **Executable PoC** — each Critical or Medium finding produces a Foundry test. If `forge test` passes, the specific exploit path works. If it fails, the finding is still reported with the test attached for manual review.
- **No LLM in the decision path** — Gemini is an optional explanation layer. Disable it with `--no-ai` and the scanner, exploit generator, and reports all continue to function identically.

---

## Features

- Regex-based heuristic predicate scoring (E/P/V/G) on externally callable functions
- Cross-chain receiver vulnerability detection
- Auto-generated Foundry exploit PoC tests, executed via `forge test`
- Optional Gemini 2.5 Flash explanation layer (detection and severity are unaffected)
- Automatic deterministic fallback when Gemini is absent or errors
- Markdown + JSON report output
- VS Code extension: inline diagnostics, findings sidebar, exploit test viewer, patch templates
- GitHub Actions CI/CD workflow included

---

## Installation

**Requirements:** Python ≥ 3.10, [Foundry](https://book.getfoundry.sh/getting-started/installation) (required for `--run-foundry`)

```bash
git clone https://github.com/your-org/trustgraph
cd trustgraph
pip install -e .
```

**With optional Gemini explanation:**

```bash
pip install -e ".[gemini]"
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.5-flash"   # optional, this is the default
```

Without a Gemini API key, TrustGraph runs deterministic-only automatically. Keys can also go in a `.env` file (gitignored):

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

# Deterministic-only (no LLM calls)
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
| Detail webview | Predicate evidence, guard verdict, Gemini explanation, Foundry result, patch template |
| Go to Source | Jump to the flagged line |
| Open Exploit Test | Open the generated `.t.sol` |
| Copy Patch | Copy the recommended fix to clipboard |

---

## Vulnerability Model

A function is flagged when it satisfies all four predicate conditions:

```
Vulnerable(f) = E(f) ∧ P(f) ∧ V(f) ∧ ¬G(f)
```

| Predicate | Meaning | How detected |
|---|---|---|
| `E(f)` | `external` or `public` visibility | Regex on function signature |
| `P(f)` | Accepts attacker-controlled payload | `bytes calldata`, `abi.decode`, param names containing `payload`/`data`/`message` |
| `V(f)` | Critical state mutation in body | `.mint(`, `.transfer(`, `.withdraw(`, `balances[` keywords |
| `G(f)` | Caller guard present | `require(msg.sender ==`, `onlyOwner`, `onlyBridge`, `trustedBridge` keywords |

**False negative note:** `G(f)` is keyword-matched within the function body only. A guard in a modifier defined on the function signature, a parent contract, or a calling function will not be detected (see [Limitations](#limitations)).

**Severity assignment:**

| Conditions | Severity |
|---|---|
| E + P + V + no guard | **Critical** |
| E + V + no guard | **Medium** |
| Otherwise | Informational |

---

## Architecture

```mermaid
flowchart TD
    A[Solidity Contracts] --> B[TrustGraph CLI]
    B --> C[Static Scanner\nE / P / V / G regex scoring]
    C --> D[Trust Assumption Inference\ndeterministic + optional Gemini explanation]
    D --> E[Guard Validation\nconfirm guard absence in function body]
    E --> F[Exploit Generator\nFoundry PoC template rendering]
    F --> G[Foundry Runner\nforge test subprocess]
    G --> H[Reports\nMarkdown + JSON]
    G --> I[VS Code Extension\ndiagnostics + investigation]
```

The pipeline is implemented as an 8-stage DAG using [LangGraph](https://github.com/langchain-ai/langgraph) as the execution framework. Each stage is a pure function operating on a typed state object; LangGraph is not used for agent planning or LLM routing — it provides DAG wiring and state passing only.

**Project layout:**

```
trustgraph/
├── cli.py              — Typer CLI entry point
├── graph.py            — 8-stage LangGraph pipeline DAG
├── models.py           — Pydantic models + WorkflowState
├── scanners/
│   └── solidity.py     — Regex function extractor + E/P/V/G scorer
├── agents/
│   ├── trust_assumption.py   — Deterministic classifier + optional Gemini call
│   ├── exploit_generator.py  — Foundry PoC template engine
│   └── patch_recommender.py  — Patch suggestion templates
├── runners/
│   └── foundry.py      — forge test subprocess wrapper
└── reports/
    ├── markdown.py     — Markdown report generator
    └── json_report.py  — JSON report generator
```

---

## Exploit Lifecycle

```mermaid
flowchart LR
    A["External Entry Point\npublic / external"] --> B["Attacker-Controlled Payload\nbytes calldata / abi.decode"]
    B --> C["Missing Guard\nno require(msg.sender == trusted)"]
    C --> D["Critical State Mutation\n.mint / .transfer / .withdraw"]
    D --> E["Generated Foundry PoC\n.t.sol"]
    E --> F["forge test\npass = exploit path confirmed"]
```

---

## Deterministic-First Design

```mermaid
flowchart LR
    subgraph det["Deterministic Scanner — sole source of truth"]
        direction TB
        A[Exposure Detection E]
        B[Payload Detection P]
        C[State Mutation Detection V]
        D[Guard Detection G]
        E[Severity Assignment]
        F[Exploit Generation]
    end
    subgraph gem["Gemini 2.5 Flash — explanation only"]
        direction TB
        G[Trust Assumption Explanation]
        H[Natural-Language Reasoning]
    end
    det --> R[Findings + Reports + PoC]
    gem -. enriches explanation .-> R
```

The Gemini layer is called once per finding after severity is already assigned, to generate a human-readable explanation of why the trust assumption is dangerous. It receives the function source and the predicate verdicts as context. Its output populates the `ai_analysis` field in reports and the VS Code panel; it cannot change `severity`, `is_vulnerable`, or exploit generation.

**Gemini fallback behaviour:**

| Condition | Behaviour |
|---|---|
| `--no-ai` passed | Deterministic only — no API call made |
| No `GEMINI_API_KEY` | Deterministic fallback, finding still reported |
| API error or quota exceeded | Deterministic fallback, finding still reported |
| Invalid or unparseable response | Deterministic fallback, finding still reported |
| Success | Finding enriched with natural-language explanation |

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

Detection is regex-based heuristic matching on individual function bodies. Current scope boundaries:

- **No modifier resolution** — `onlyOwner` on the function signature is not detected as a guard; only `require(...)` statements inside the function body are checked
- **No inheritance resolution** — guards defined in a parent contract are not traced
- **No cross-function taint tracking** — a guard in a wrapper or calling function is not propagated
- **Single-file scope** — data flows across multiple `.sol` files are not tracked
- **Keyword sensitivity** — non-standard naming conventions may produce false negatives or false positives

---

## Future Work

- AST-level parsing via `solc --ast-compact-json` to replace regex extraction
- Modifier and inheritance resolution
- Cross-function and cross-contract call graph analysis
- Slither integration as a complementary analysis pass
- Semgrep rule export from confirmed findings

---

## Security Disclaimer

TrustGraph is a research prototype and has not been security-audited.

A passing Foundry test confirms that the generated exploit PoC executes successfully against the generated test harness — it does not prove the absence of other attack paths or that the contract is otherwise safe. All findings require human review. TrustGraph does not replace a professional smart contract audit.

---

## Acknowledgements

Motivated by the August 2024 CrossCurve bridge exploit and the broader class of trust-boundary failures in cross-chain receiver architectures.
