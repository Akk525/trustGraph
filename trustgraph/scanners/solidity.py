from __future__ import annotations

import re
from pathlib import Path

from trustgraph.models import (
    FunctionInfo,
    RiskLevel,
    ScoreBreakdown,
    ScanResult,
)

# ── Heuristic keyword sets ────────────────────────────────────────────────────

_PAYLOAD_KEYWORDS = [
    r"\bbytes\b",
    r"\bcalldata\b",
    r"\bpayload\b",
    r"\bmessage\b",
    r"abi\.decode\b",
    r"\bdata\b",
]

_MUTATION_PATTERNS = [
    r"\.mint\s*\(",
    r"\bmint\s*\(",
    r"_mint\s*\(",
    r"\.transfer\s*\(",
    r"\.transferFrom\s*\(",
    r"\.safeTransfer\s*\(",
    r"\.safeTransferFrom\s*\(",
    r"\.withdraw\s*\(",
    r"\bwithdraw\s*\(",
    r"\.unlock\s*\(",
    r"\bunlock\s*\(",
    r"\.burn\s*\(",
    r"\bburn\s*\(",
    r"_burn\s*\(",
    r"balances\s*\[",
]

_GUARD_PATTERNS = [
    r"\bonlyOwner\b",
    r"\bonlyRole\b",
    r"\bonlyBridge\b",
    r"\bonlyCaller\b",
    r"\bonlyAuthorized\b",
    r"require\s*\(\s*msg\.sender\s*==",
    r"require\s*\(\s*_msgSender\s*\(\)\s*==",
    r"\btrustedBridge\b",
    r"\btrustedRemote\b",
    r"\bsourceChain\b",
    r"\bsourceAddress\b",
    r"\becrecover\b",
    r"\bisValidSignature\b",
    r"\bverify\s*\(",
    r"\bendpoint\b",
    r"\bILayerZeroEndpoint\b",
    r"\bsignature\b",
]

_CC_RECEIVER_NAMES = {
    "receiveMessage",
    "lzReceive",
    "ccipReceive",
    "executeMessage",
    "anyExecute",
    "handle",
    "receivePayload",
    "onMessageReceived",
    "handleMessage",
    "execute",
    "processMessage",
}


# ── Signature parser ──────────────────────────────────────────────────────────

def _parse_sig(raw: str) -> tuple[str | None, str, str, list[str]]:
    """Parse a raw function signature into (name, params, visibility, modifiers)."""
    # Strip returns(...)
    cleaned = re.sub(r"\breturns\s*\([^)]*\)", "", raw, flags=re.DOTALL)
    # Strip opening brace and trailing semicolon
    cleaned = cleaned.replace("{", "").replace(";", "").strip()

    m = re.search(
        r"function\s+(\w+)\s*\(([^)]*)\)\s*(.*)",
        cleaned,
        re.DOTALL,
    )
    if not m:
        return None, "", "internal", []

    name = m.group(1)
    params = m.group(2).strip()
    after = m.group(3).strip()

    tokens = re.findall(r"\w+", after)
    visibility = "internal"
    modifiers: list[str] = []

    for tok in tokens:
        if tok in ("public", "external", "internal", "private"):
            visibility = tok
        elif tok not in ("view", "pure", "payable", "virtual", "override"):
            modifiers.append(tok)

    return name, params, visibility, modifiers


# ── Function extractor ────────────────────────────────────────────────────────

def extract_functions(source: str, filepath: str) -> list[FunctionInfo]:
    """Extract public/external function definitions from Solidity source."""
    lines = source.split("\n")
    results: list[FunctionInfo] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Skip single-line comments and blank lines
        if stripped.startswith("//") or stripped.startswith("*") or not stripped:
            i += 1
            continue

        if not re.search(r"\bfunction\s+\w+\s*\(", stripped):
            i += 1
            continue

        start_line = i + 1  # 1-indexed

        # Collect signature until we find '{' or ';' (abstract/interface)
        sig_parts: list[str] = []
        j = i
        abstract = False
        while j < len(lines):
            sig_parts.append(lines[j])
            if "{" in lines[j]:
                break
            if ";" in lines[j]:
                abstract = True
                break
            j += 1

        raw_sig = " ".join(sig_parts)
        name, params, visibility, modifiers = _parse_sig(raw_sig)

        if name is None or visibility not in ("public", "external"):
            # Still need to advance past the body if there is one
            if not abstract:
                depth = 0
                k = j
                started = False
                while k < len(lines):
                    for ch in lines[k]:
                        if ch == "{":
                            depth += 1
                            started = True
                        elif ch == "}":
                            depth -= 1
                    if started and depth == 0:
                        break
                    k += 1
                i = k + 1
            else:
                i = j + 1
            continue

        if abstract:
            i = j + 1
            continue

        # Extract body via brace counting
        body_lines: list[str] = []
        depth = 0
        k = j
        started = False
        while k < len(lines):
            body_lines.append(lines[k])
            for ch in lines[k]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
            if started and depth == 0:
                break
            k += 1

        body = "\n".join(body_lines)

        results.append(
            FunctionInfo(
                file=filepath,
                line=start_line,
                name=name,
                visibility=visibility,
                params=params,
                modifiers=modifiers,
                body=body,
            )
        )
        i = k + 1

    return results


# ── Heuristic scoring ─────────────────────────────────────────────────────────

def _match_any(text: str, patterns: list[str]) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def score_function(func: FunctionInfo) -> ScanResult:
    full_text = func.params + "\n" + func.body
    modifier_text = " ".join(func.modifiers)

    exposure = func.visibility in ("public", "external")

    matched_payload = _match_any(func.params, _PAYLOAD_KEYWORDS)
    if not matched_payload:
        matched_payload = _match_any(func.body, [r"abi\.decode\b"])
    payload = bool(matched_payload)

    matched_mutation = _match_any(func.body, _MUTATION_PATTERNS)
    mutation = bool(matched_mutation)

    guard_in_modifiers = _match_any(modifier_text, _GUARD_PATTERNS)
    guard_in_body = _match_any(func.body, _GUARD_PATTERNS)
    guard = bool(guard_in_modifiers or guard_in_body)

    scores = ScoreBreakdown(
        exposure=exposure,
        payload=payload,
        mutation=mutation,
        guard=guard,
    )

    evidence: list[str] = []
    if exposure:
        evidence.append(f"visibility={func.visibility}")
    if payload:
        evidence.append(f"payload params/decode detected")
    if mutation:
        evidence.append(f"critical state mutation: {matched_mutation[0] if matched_mutation else '?'}")
    if guard:
        evidence.append(f"guard found: {(guard_in_modifiers or guard_in_body)[0]}")
    else:
        evidence.append("no access-control guard detected")

    if exposure and payload and mutation and not guard:
        risk = RiskLevel.CRITICAL
    elif exposure and mutation and not guard:
        risk = RiskLevel.MEDIUM
    else:
        risk = RiskLevel.INFORMATIONAL

    return ScanResult(
        function_info=func,
        scores=scores,
        risk_level=risk,
        evidence=evidence,
    )


# ── Top-level scan ────────────────────────────────────────────────────────────

def scan_file(filepath: str) -> list[ScanResult]:
    source = Path(filepath).read_text(encoding="utf-8", errors="replace")
    functions = extract_functions(source, filepath)
    return [score_function(f) for f in functions]


def scan_directory(path: str) -> list[ScanResult]:
    results: list[ScanResult] = []
    for sol_file in Path(path).rglob("*.sol"):
        results.extend(scan_file(str(sol_file)))
    return results


def is_cross_chain_receiver(func_name: str) -> bool:
    return func_name in _CC_RECEIVER_NAMES
