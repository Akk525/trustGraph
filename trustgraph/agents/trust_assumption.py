from __future__ import annotations

import json
import os
import re

from trustgraph.models import (
    FunctionInfo,
    GeminiClassification,
    ScanResult,
    TrustAssumption,
    VulnerabilityCategory,
)
from trustgraph.scanners.solidity import is_cross_chain_receiver

# ── Optional Gemini SDK import ────────────────────────────────────────────────
try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_MODEL = "gemini-2.5-flash"

LLM_STATUS_SUCCESS = "gemini_success"
LLM_STATUS_NO_KEY = "missing_api_key_fallback"
LLM_STATUS_NO_AI = "no_ai_forced"
LLM_STATUS_PARSE_FAIL = "failed_parse_fallback"
LLM_STATUS_API_ERROR = "gemini_error_fallback"
LLM_STATUS_SDK_MISSING = "sdk_not_installed_fallback"

_FALLBACK_DISPLAY: dict[str, str] = {
    LLM_STATUS_NO_KEY: "No Gemini API key found; deterministic fallback used.",
    LLM_STATUS_NO_AI: "AI disabled; deterministic fallback used.",
    LLM_STATUS_PARSE_FAIL: "Gemini response could not be parsed; deterministic fallback used.",
    LLM_STATUS_SDK_MISSING: "Gemini SDK not installed; deterministic fallback used.",
}


def _api_error_display(exc: Exception) -> str:
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return "Gemini quota exceeded; deterministic fallback used."
    return "Gemini API error; deterministic fallback used."

_FUNCTION_TYPE_MAP: dict[str, VulnerabilityCategory] = {
    "cross_chain_receiver": VulnerabilityCategory.CROSS_CHAIN_RECEIVER,
    "privileged_operation": VulnerabilityCategory.GENERIC_EXTERNAL,
    "generic_external": VulnerabilityCategory.GENERIC_EXTERNAL,
    "unknown": VulnerabilityCategory.UNKNOWN,
}


# ── Deterministic classifier (always available) ───────────────────────────────

def _deterministic_classify(func_info: FunctionInfo) -> TrustAssumption:
    name = func_info.name

    if is_cross_chain_receiver(name):
        return TrustAssumption(
            function_name=name,
            category=VulnerabilityCategory.CROSS_CHAIN_RECEIVER,
            assumed_trusted_caller=(
                "only the trusted bridge or messaging endpoint (e.g. LayerZero endpoint, "
                "CCIP router, Axelar gateway) should be permitted to call this function"
            ),
            attack_vector=(
                "direct external invocation with a forged payload — an attacker calls "
                f"{name}() directly, bypassing the bridge entirely"
            ),
        )

    return TrustAssumption(
        function_name=name,
        category=VulnerabilityCategory.GENERIC_EXTERNAL,
        assumed_trusted_caller=(
            "an authorised caller (owner, role holder, or specific contract) should "
            "be the only entity permitted to trigger this state mutation"
        ),
        attack_vector=(
            f"direct external invocation of {name}() with attacker-controlled arguments "
            "causes unintended state mutation without authorisation"
        ),
    )


# ── Gemini helpers ────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences if the model returns them despite instructions."""
    text = text.strip()
    # Match ```json ... ``` or ``` ... ```
    fenced = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    # Single-line fence: strip leading ``` and trailing ```
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop ```json line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _build_prompt(func_info: FunctionInfo, scan_result: ScanResult) -> str:
    modifiers_str = ", ".join(func_info.modifiers) if func_info.modifiers else "none"
    evidence_str = "\n".join(f"  - {e}" for e in scan_result.evidence)

    return f"""You are a specialist in smart contract security, specifically cross-chain trust boundary analysis.

ROLE: Classify the trust assumption violated by this Solidity function and explain the attack vector.
You are NOT responsible for determining severity — the deterministic scanner does that.
Only reason about what is explicitly present in the provided code.
Do not invent imports, state variables, or behaviours not shown.

--- FUNCTION DETAILS ---
Contract file : {func_info.file}
Function name : {func_info.name}
Signature     : function {func_info.name}({func_info.params}) {func_info.visibility}
Modifiers     : {modifiers_str}

--- FUNCTION BODY ---
{func_info.body}

--- DETERMINISTIC SCANNER EVIDENCE ---
Exposure (external/public) : {scan_result.scores.exposure}
Payload parameter detected  : {scan_result.scores.payload}
Critical state mutation     : {scan_result.scores.mutation}
Access-control guard found  : {scan_result.scores.guard}
Evidence items:
{evidence_str}

--- INSTRUCTIONS ---
Return ONLY a JSON object. No markdown, no explanation outside the JSON.

Required schema (use these exact keys):
{{
  "function_type": "cross_chain_receiver | privileged_operation | generic_external | unknown",
  "intended_caller": "<who should legitimately call this function>",
  "implicit_assumption": "<what trust assumption is implicitly being made>",
  "missing_enforcement": "<what on-chain check is absent>",
  "attack_vector": "<how an attacker exploits the missing guard>",
  "confidence": <float 0.0–1.0>,
  "reasoning_summary": "<1-2 sentence reasoning summary>"
}}"""


def _call_gemini_api(prompt: str, model: str, api_key: str) -> str:
    client = _genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=_genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
    except Exception:
        # Some model/region combos don't support response_mime_type — retry plain
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
    return response.text or ""


def _gemini_classify(
    func_info: FunctionInfo,
    scan_result: ScanResult,
    api_key: str,
) -> TrustAssumption:
    model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
    base = _deterministic_classify(func_info)  # deterministic fallback always ready

    # ── API call ──────────────────────────────────────────────────────────────
    try:
        raw = _call_gemini_api(_build_prompt(func_info, scan_result), model, api_key)
    except Exception as exc:
        return base.model_copy(update={
            "llm_status": LLM_STATUS_API_ERROR,
            "llm_display_message": _api_error_display(exc),
            "ai_provider": "gemini",
            "gemini_model": model,
            "reasoning_summary": f"Gemini API error: {exc}",
        })

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        cleaned = _strip_json_fences(raw)
        parsed = GeminiClassification.model_validate(json.loads(cleaned))
    except Exception:
        return base.model_copy(update={
            "llm_status": LLM_STATUS_PARSE_FAIL,
            "llm_display_message": _FALLBACK_DISPLAY[LLM_STATUS_PARSE_FAIL],
            "ai_provider": "gemini",
            "gemini_model": model,
        })

    category = _FUNCTION_TYPE_MAP.get(
        parsed.function_type, VulnerabilityCategory.UNKNOWN
    )

    return TrustAssumption(
        function_name=func_info.name,
        category=category,
        assumed_trusted_caller=parsed.intended_caller,
        attack_vector=parsed.attack_vector,
        llm_status=LLM_STATUS_SUCCESS,
        ai_provider="gemini",
        gemini_model=model,
        confidence=parsed.confidence,
        reasoning_summary=parsed.reasoning_summary,
        implicit_assumption=parsed.implicit_assumption,
        missing_enforcement=parsed.missing_enforcement,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def infer_trust_assumption(
    scan_result: ScanResult,
    no_ai: bool = True,
) -> TrustAssumption:
    """
    Classify the trust assumption for a flagged function.

    Decision tree:
      --no-ai passed            → deterministic, status=no_ai_forced
      GEMINI_API_KEY missing    → deterministic, status=missing_api_key_fallback
      google-genai not installed → deterministic, status=sdk_not_installed_fallback
      Gemini API error          → deterministic, status=gemini_error_fallback
      Gemini bad JSON           → deterministic, status=failed_parse_fallback
      Gemini success            → Gemini result,  status=gemini_success
    """
    func_info = scan_result.function_info

    if no_ai:
        base = _deterministic_classify(func_info)
        return base.model_copy(update={
            "llm_status": LLM_STATUS_NO_AI,
            "llm_display_message": _FALLBACK_DISPLAY[LLM_STATUS_NO_AI],
        })

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        base = _deterministic_classify(func_info)
        return base.model_copy(update={
            "llm_status": LLM_STATUS_NO_KEY,
            "llm_display_message": _FALLBACK_DISPLAY[LLM_STATUS_NO_KEY],
        })

    if not _GENAI_AVAILABLE:
        base = _deterministic_classify(func_info)
        return base.model_copy(update={
            "llm_status": LLM_STATUS_SDK_MISSING,
            "llm_display_message": _FALLBACK_DISPLAY[LLM_STATUS_SDK_MISSING],
            "reasoning_summary": "Install google-genai: pip install -e '[gemini]'",
        })

    return _gemini_classify(func_info, scan_result, api_key)
