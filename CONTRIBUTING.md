# Contributing to TrustGraph

---

## Development Setup

```bash
git clone https://github.com/your-org/trustgraph
cd trustgraph
pip install -e ".[gemini,dev]"
```

The `dev` extras install `pytest` and `pytest-mock`. Foundry must be installed separately for tests that invoke `forge test`:

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

---

## Running Tests

```bash
pytest
```

Tests that require `forge` are skipped automatically if Foundry is not installed. The CI workflow runs the full suite including Foundry execution.

---

## What Contributions Are Welcome

**New vulnerability predicates.** The core scanner (`trustgraph/scanners/solidity.py`) evaluates four conditions (E/P/V/G). Adding new detection patterns for known vulnerability classes (e.g. new payload parameter conventions, additional state-mutation patterns, additional guard keywords) is a direct and useful contribution.

**Exploit template improvements.** `trustgraph/agents/exploit_generator.py` produces Foundry PoC templates. Better templates, more contract-type coverage, or improved harness setup are useful.

**Additional example contracts.** `examples/` currently contains one vulnerable receiver. Safe counterparts (guarded receivers that should produce zero findings) and new vulnerability class examples are valuable for verifying both detection and false-positive rate.

**VS Code extension improvements.** The extension lives in `vscode-extension/`. Bug fixes, improved webview layout, and additional command palette actions are welcome. See [VS Code Extension](#vs-code-extension) below.

**Bug reports.** False positives, false negatives, CLI errors, and Foundry runner failures are all useful. Include the contract snippet and the `trustgraph` command used.

---

## Design Constraint: Deterministic-First

This is the hardest constraint in the codebase.

**The scanner, severity assignment, and exploit generation must remain LLM-free.** Any contribution that routes a finding, severity score, or exploit decision through a model will be rejected, regardless of accuracy claims.

Gemini is called in `trustgraph/agents/trust_assumption.py` after severity is already assigned, for explanation only. Its output goes to `ai_analysis` in the finding. It cannot alter `is_vulnerable`, `severity`, or trigger exploit generation.

If you want to improve detection quality, improve the predicate rules in `solidity.py` — not the Gemini prompt.

---

## Adding a New Detection Pattern

1. Add the pattern to the relevant predicate in `trustgraph/scanners/solidity.py`.
2. Add a test contract in `examples/` that triggers the pattern.
3. Add a test in `tests/` that asserts the correct finding is produced.
4. Add a test contract in `examples/` where the guard is present and assert no Critical/Medium finding is produced.

A new pattern without a false-positive test will not be merged.

---

## VS Code Extension

The extension is a standard VS Code extension project in `vscode-extension/`.

```bash
cd vscode-extension
npm install
npm run compile
```

Press **F5** in VS Code to launch the Extension Development Host for manual testing.

Lint before submitting:

```bash
npm run lint
```

---

## Pull Request Checklist

- [ ] `pytest` passes locally
- [ ] New detection patterns include both a positive test (detected) and a negative test (not falsely detected)
- [ ] No LLM call added to the detection, severity, or exploit generation path
- [ ] No new dependency added without discussion in the issue first
- [ ] VS Code extension changes pass `npm run lint`

---

## Reporting Vulnerabilities in TrustGraph Itself

See [SECURITY.md](SECURITY.md).
