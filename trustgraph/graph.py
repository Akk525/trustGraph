from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, StateGraph

from trustgraph.agents.exploit_generator import generate_exploit_test
from trustgraph.agents.patch_recommender import recommend_patch
from trustgraph.agents.trust_assumption import infer_trust_assumption
from trustgraph.models import (
    Finding,
    FunctionInfo,
    PatchRecommendation,
    RiskLevel,
    ScanResult,
    TrustAssumption,
    WorkflowState,
)
from trustgraph.reports.json_report import generate_json_report
from trustgraph.reports.markdown import generate_markdown_report
from trustgraph.runners.foundry import find_foundry_root, run_forge_test
from trustgraph.scanners.solidity import extract_functions, score_function


# ── Node implementations ──────────────────────────────────────────────────────

def load_contracts(state: WorkflowState) -> dict:
    path = Path(state["config"]["path"])
    sol_files: list[str] = []

    if path.is_file() and path.suffix == ".sol":
        sol_files = [str(path)]
    elif path.is_dir():
        sol_files = [str(f) for f in sorted(path.rglob("*.sol"))]
    else:
        return {
            "solidity_files": [],
            "errors": state.get("errors", []) + [f"Path not found or not Solidity: {path}"],
        }

    return {"solidity_files": sol_files}


def static_scan(state: WorkflowState) -> dict:
    all_functions: list[dict] = []
    candidates: list[dict] = []

    for filepath in state["solidity_files"]:
        try:
            source = Path(filepath).read_text(encoding="utf-8", errors="replace")
            funcs = extract_functions(source, filepath)
            for func in funcs:
                sr = score_function(func)
                all_functions.append(func.model_dump())
                if sr.risk_level in (RiskLevel.CRITICAL, RiskLevel.MEDIUM):
                    candidates.append({
                        "scan_result": sr.model_dump(),
                        "risk_level": sr.risk_level.value,
                    })
        except OSError as e:
            state.get("errors", []).append(f"Failed to read {filepath}: {e}")

    return {
        "raw_functions": all_functions,
        "findings": candidates,
    }


def infer_trust_assumptions(state: WorkflowState) -> dict:
    no_ai = state["config"].get("no_ai", True)
    updated: list[dict] = []

    for fd in state["findings"]:
        sr = ScanResult.model_validate(fd["scan_result"])
        ta = infer_trust_assumption(sr, no_ai=no_ai)
        fd = dict(fd)
        fd["trust_assumption"] = ta.model_dump()
        updated.append(fd)

    return {"findings": updated}


def validate_guards(state: WorkflowState) -> dict:
    """Re-confirm guard status and enrich evidence. Deterministic."""
    updated: list[dict] = []

    for fd in state["findings"]:
        fd = dict(fd)
        sr = ScanResult.model_validate(fd["scan_result"])

        # Add explicit note if guard is completely absent
        if not sr.scores.guard:
            extra = "CONFIRMED: no access-control guard in modifiers or body"
            evidence = sr.evidence[:]
            if extra not in evidence:
                evidence.append(extra)
            sr_dict = fd["scan_result"].copy()
            sr_dict["evidence"] = evidence
            fd["scan_result"] = sr_dict

        updated.append(fd)

    return {"findings": updated}


def generate_exploit_tests(state: WorkflowState) -> dict:
    config = state["config"]
    output_dir = str(Path(config["output_dir"]) / "tests")

    # Find foundry root from the input path so generated tests land in test/
    foundry_root = find_foundry_root(config["path"])
    foundry_test_dir = str(Path(foundry_root) / "test") if foundry_root else None

    updated: list[dict] = []

    for fd in state["findings"]:
        fd = dict(fd)
        if fd.get("risk_level") != RiskLevel.CRITICAL.value:
            updated.append(fd)
            continue

        try:
            finding = Finding.model_validate({
                **fd,
                "exploit_path": None,
                "foundry_result": None,
                "patch": None,
            })
            exploit_path = generate_exploit_test(finding, output_dir, foundry_test_dir)
            fd["exploit_path"] = exploit_path
        except Exception as e:
            fd["exploit_path"] = None
            fd.setdefault("errors", []).append(f"Exploit generation failed: {e}")

        updated.append(fd)

    return {"findings": updated}


def run_foundry_tests(state: WorkflowState) -> dict:
    config = state["config"]
    foundry_root = find_foundry_root(config["path"])

    if not foundry_root:
        msg = "No foundry.toml found in or above the scan path; skipping forge test."
        updated = []
        for fd in state["findings"]:
            fd = dict(fd)
            if fd.get("exploit_path"):
                fd["foundry_result"] = {
                    "ran": False,
                    "passed": None,
                    "output": msg,
                    "test_path": fd.get("exploit_path", ""),
                }
            updated.append(fd)
        return {"findings": updated}

    # Run the full suite once from the foundry root
    result = run_forge_test(foundry_root)
    result_base = result.model_dump()

    updated = []
    for fd in state["findings"]:
        fd = dict(fd)
        if fd.get("exploit_path") or fd.get("risk_level") == RiskLevel.CRITICAL.value:
            # Stamp each finding's own exploit path so test_path is never empty.
            fd["foundry_result"] = {**result_base, "test_path": fd.get("exploit_path") or ""}
        updated.append(fd)

    return {"findings": updated}


def recommend_patches(state: WorkflowState) -> dict:
    updated: list[dict] = []

    for fd in state["findings"]:
        fd = dict(fd)
        try:
            finding = Finding.model_validate({
                **fd,
                "exploit_path": fd.get("exploit_path"),
                "foundry_result": fd.get("foundry_result"),
                "patch": None,
            })
            patch = recommend_patch(finding)
            fd["patch"] = patch.model_dump()
        except Exception as e:
            fd["patch"] = PatchRecommendation(
                description="Patch recommendation unavailable.",
                code_snippet=f"// Error: {e}",
            ).model_dump()

        updated.append(fd)

    return {"findings": updated}


def generate_report(state: WorkflowState) -> dict:
    config = state["config"]
    fmt = config.get("report_format", "markdown")
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []

    if fmt in ("markdown", "both"):
        md = generate_markdown_report(state)
        p = output_dir / "report.md"
        p.write_text(md, encoding="utf-8")
        paths.append(str(p))

    if fmt in ("json", "both"):
        js = generate_json_report(state)
        p = output_dir / "report.json"
        p.write_text(js, encoding="utf-8")
        paths.append(str(p))

    return {"report_paths": paths}


# ── Conditional routing ───────────────────────────────────────────────────────

def _route_after_validate(state: WorkflowState) -> str:
    if state["config"].get("generate_test", True):
        return "generate_exploit_tests"
    return "recommend_patches"


def _route_after_exploit(state: WorkflowState) -> str:
    if state["config"].get("run_foundry", False):
        return "run_foundry_tests"
    return "recommend_patches"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(WorkflowState)

    g.add_node("load_contracts", load_contracts)
    g.add_node("static_scan", static_scan)
    g.add_node("infer_trust_assumptions", infer_trust_assumptions)
    g.add_node("validate_guards", validate_guards)
    g.add_node("generate_exploit_tests", generate_exploit_tests)
    g.add_node("run_foundry_tests", run_foundry_tests)
    g.add_node("recommend_patches", recommend_patches)
    g.add_node("generate_report", generate_report)

    g.set_entry_point("load_contracts")
    g.add_edge("load_contracts", "static_scan")
    g.add_edge("static_scan", "infer_trust_assumptions")
    g.add_edge("infer_trust_assumptions", "validate_guards")

    g.add_conditional_edges(
        "validate_guards",
        _route_after_validate,
        {
            "generate_exploit_tests": "generate_exploit_tests",
            "recommend_patches": "recommend_patches",
        },
    )

    g.add_conditional_edges(
        "generate_exploit_tests",
        _route_after_exploit,
        {
            "run_foundry_tests": "run_foundry_tests",
            "recommend_patches": "recommend_patches",
        },
    )

    g.add_edge("run_foundry_tests", "recommend_patches")
    g.add_edge("recommend_patches", "generate_report")
    g.add_edge("generate_report", END)

    return g


def run_workflow(config: dict) -> WorkflowState:
    initial: WorkflowState = {
        "solidity_files": [],
        "raw_functions": [],
        "findings": [],
        "report_paths": [],
        "config": config,
        "errors": [],
    }

    graph = build_graph().compile()
    return graph.invoke(initial)
