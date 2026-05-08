from __future__ import annotations

import json
from datetime import date

from trustgraph.models import Finding, WorkflowState


def generate_json_report(state: WorkflowState) -> str:
    config = state["config"]
    findings_raw = state.get("findings", [])

    structured_findings = []
    for fd in findings_raw:
        finding = Finding.model_validate(fd)
        fi = finding.scan_result.function_info
        ta = finding.trust_assumption
        sr = finding.scan_result

        entry: dict = {
            "severity": sr.risk_level.value,
            "file": fi.file,
            "line": fi.line,
            "function": fi.name,
            "visibility": fi.visibility,
            "modifiers": fi.modifiers,
            "category": ta.category.value,
            "trust_assumption": {
                "assumed_trusted_caller": ta.assumed_trusted_caller,
                "attack_vector": ta.attack_vector,
                "implicit_assumption": ta.implicit_assumption,
                "missing_enforcement": ta.missing_enforcement,
            },
            "ai_analysis": {
                "llm_status": ta.llm_status,
                "llm_display_message": ta.llm_display_message,
                "ai_provider": ta.ai_provider,
                "gemini_model": ta.gemini_model,
                "confidence": ta.confidence,
                "reasoning_summary": ta.reasoning_summary,
            },
            "scores": {
                "exposure": sr.scores.exposure,
                "payload": sr.scores.payload,
                "mutation": sr.scores.mutation,
                "guard": sr.scores.guard,
            },
            "evidence": sr.evidence,
            "exploit_path": finding.exploit_path,
            "patch": (
                {
                    "description": finding.patch.description,
                    "code_snippet": finding.patch.code_snippet,
                }
                if finding.patch
                else None
            ),
            "foundry": (
                {
                    "ran": finding.foundry_result.ran,
                    "passed": finding.foundry_result.passed,
                    "test_path": finding.foundry_result.test_path,
                    "output_summary": finding.foundry_result.output[:500],
                }
                if finding.foundry_result
                else None
            ),
        }
        structured_findings.append(entry)

    # Aggregate AI status across findings
    ai_statuses = [
        f.get("trust_assumption", {}).get("llm_status", "no_ai_forced")
        for f in findings_raw
    ]
    gemini_hits = sum(1 for s in ai_statuses if s == "gemini_success")
    fallback_hits = len(ai_statuses) - gemini_hits

    report = {
        "tool": "TrustGraph",
        "version": "0.1.0",
        "generated": date.today().isoformat(),
        "scan_path": config.get("path", ""),
        "summary": {
            "files_scanned": len(state.get("solidity_files", [])),
            "functions_analysed": len(state.get("raw_functions", [])),
            "critical": sum(1 for f in findings_raw if f.get("risk_level") == "Critical"),
            "medium": sum(1 for f in findings_raw if f.get("risk_level") == "Medium"),
            "ai_analysis": {
                "gemini_success_count": gemini_hits,
                "fallback_count": fallback_hits,
            },
        },
        "findings": structured_findings,
        "errors": state.get("errors", []),
    }

    return json.dumps(report, indent=2)
