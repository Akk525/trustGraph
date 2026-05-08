from __future__ import annotations

from datetime import date
from pathlib import Path

from trustgraph.models import Finding, RiskLevel, WorkflowState

_RISK_EMOJI = {
    RiskLevel.CRITICAL: "🔴",
    RiskLevel.MEDIUM: "🟡",
    RiskLevel.INFORMATIONAL: "🔵",
}


def _severity_badge(level: str) -> str:
    return _RISK_EMOJI.get(RiskLevel(level), "") + " " + level


def _ai_status_label(ta: object) -> str:
    status = getattr(ta, "llm_status", "no_ai_forced")
    provider = getattr(ta, "ai_provider", None)
    model = getattr(ta, "gemini_model", None)
    if provider and model:
        return f"`{status}` via {provider} ({model})"
    return f"`{status}`"


def _finding_section(idx: int, f: Finding) -> str:
    sr = f.scan_result
    ta = f.trust_assumption
    fi = sr.function_info
    level = sr.risk_level.value

    lines = [
        f"### [{level}-{idx:03d}] `{fi.name}` in `{Path(fi.file).name}`",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Severity** | {_severity_badge(level)} |",
        f"| **File** | `{fi.file}:{fi.line}` |",
        f"| **Function** | `{fi.name}({fi.params})` |",
        f"| **Visibility** | `{fi.visibility}` |",
        f"| **Modifiers** | `{', '.join(fi.modifiers) or 'none'}` |",
        f"| **Category** | `{ta.category.value}` |",
        f"| **AI Analysis** | {_ai_status_label(ta)} |",
    ]

    if ta.confidence is not None:
        lines.append(f"| **Confidence** | {ta.confidence:.0%} |")

    lines += [
        "",
        "#### Trust Assumption Violated",
        "",
        ta.assumed_trusted_caller,
        "",
    ]

    if ta.implicit_assumption:
        lines += [
            "#### Implicit Assumption (Gemini)",
            "",
            ta.implicit_assumption,
            "",
        ]

    if ta.missing_enforcement:
        lines += [
            "#### Missing Enforcement (Gemini)",
            "",
            ta.missing_enforcement,
            "",
        ]

    if ta.reasoning_summary and ta.ai_provider:
        lines += [
            "#### Reasoning Summary (Gemini)",
            "",
            f"_{ta.reasoning_summary}_",
            "",
        ]

    lines += [
        "#### Evidence",
        "",
    ]
    for e in sr.evidence:
        lines.append(f"- {e}")

    lines += [
        "",
        "#### Attack Vector",
        "",
        ta.attack_vector,
        "",
    ]

    if f.exploit_path:
        lines += [
            "#### Generated Exploit Test",
            "",
            f"`{f.exploit_path}`",
            "",
        ]

    if f.foundry_result:
        fr = f.foundry_result
        if fr.ran:
            status = "PASS" if fr.passed else "FAIL"
            lines += [
                "#### Foundry Result",
                "",
                f"**Status:** `{status}`",
                "",
                "```",
                fr.output[:3000].strip(),
                "```",
                "",
            ]
        else:
            lines += [
                "#### Foundry Result",
                "",
                fr.output,
                "",
            ]

    if f.patch:
        lines += [
            "#### Suggested Patch",
            "",
            f.patch.description,
            "",
            "```solidity",
            f.patch.code_snippet.strip(),
            "```",
            "",
        ]

    return "\n".join(lines)


def _scan_ai_summary(findings_raw: list[dict]) -> str:
    statuses = []
    for fd in findings_raw:
        ta_dict = fd.get("trust_assumption", {})
        statuses.append(ta_dict.get("llm_status", "no_ai_forced"))
    if not statuses:
        return "no findings"
    if any(s == "gemini_success" for s in statuses):
        models = {
            fd.get("trust_assumption", {}).get("gemini_model", "?")
            for fd in findings_raw
            if fd.get("trust_assumption", {}).get("llm_status") == "gemini_success"
        }
        return f"Gemini ({', '.join(sorted(models))})"
    first = statuses[0]
    return {"no_ai_forced": "deterministic (--no-ai)", "missing_api_key_fallback": "deterministic (no key)"}.get(
        first, f"deterministic ({first})"
    )


def generate_markdown_report(state: WorkflowState) -> str:
    config = state["config"]
    scan_path = config.get("path", "unknown")
    findings_raw = state.get("findings", [])

    critical = [f for f in findings_raw if f.get("risk_level") == RiskLevel.CRITICAL.value]
    medium = [f for f in findings_raw if f.get("risk_level") == RiskLevel.MEDIUM.value]

    header = [
        "# TrustGraph Security Report",
        "",
        f"**Generated:** {date.today().isoformat()}  ",
        f"**Scan Path:** `{scan_path}`  ",
        f"**Tool Version:** 0.1.0  ",
        f"**Trust Analysis:** {_scan_ai_summary(findings_raw)}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Files scanned | {len(state.get('solidity_files', []))} |",
        f"| Functions analysed | {len(state.get('raw_functions', []))} |",
        f"| Critical findings | {len(critical)} |",
        f"| Medium findings | {len(medium)} |",
        "",
        "---",
        "",
        "## Findings",
        "",
    ]

    if not findings_raw:
        header.append("_No significant findings._")
    else:
        for idx, fd in enumerate(findings_raw, start=1):
            finding = Finding.model_validate(fd)
            header.append(_finding_section(idx, finding))
            header.append("---")
            header.append("")

    footer = [
        "## Limitations",
        "",
        "- Analysis is heuristic-based and may produce false positives or miss "
        "vulnerabilities that require semantic understanding.",
        "- Guard detection relies on keyword patterns and may miss custom modifier logic.",
        "- Cross-function or cross-contract data-flow is not tracked.",
        "",
        "## Future Work",
        "",
        "- AST-level parsing via `solc --ast-compact-json` for higher precision.",
        "- Taint analysis to trace attacker-controlled data through call chains.",
        "- Integration with Slither for complementary static analysis.",
        "- LLM-assisted trust boundary reasoning (see `--no-ai` flag).",
        "",
        "_Generated by [TrustGraph](https://github.com/your-org/trustgraph)_",
    ]

    return "\n".join(header + footer)
