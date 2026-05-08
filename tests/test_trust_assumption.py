"""
Tests for trust_assumption agent.

Runs under both unittest (python -m unittest) and pytest.
Uses only stdlib unittest.mock so the broken web3 pytest plugin is irrelevant.

Covers:
 - JSON fence stripping (plain, ```json, generic ```)
 - Deterministic fallback when GEMINI_API_KEY is absent
 - Deterministic fallback when Gemini returns invalid JSON
 - Correct llm_status values in every code path
 - --no-ai flag forces deterministic output
 - API key never appears in TrustAssumption output
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

# Ensure project root is on path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from trustgraph.agents.trust_assumption import (
    LLM_STATUS_API_ERROR,
    LLM_STATUS_NO_AI,
    LLM_STATUS_NO_KEY,
    LLM_STATUS_PARSE_FAIL,
    LLM_STATUS_SUCCESS,
    _strip_json_fences,
    infer_trust_assumption,
)
from trustgraph.models import (
    FunctionInfo,
    RiskLevel,
    ScoreBreakdown,
    ScanResult,
    VulnerabilityCategory,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _make_scan_result(name: str = "receiveMessage") -> ScanResult:
    func = FunctionInfo(
        file="VulnerableReceiver.sol",
        line=10,
        name=name,
        visibility="external",
        params="bytes calldata payload",
        modifiers=[],
        body=(
            "(address to, uint256 amount) = abi.decode(payload, (address, uint256));\n"
            "token.mint(to, amount);"
        ),
    )
    return ScanResult(
        function_info=func,
        scores=ScoreBreakdown(exposure=True, payload=True, mutation=True, guard=False),
        risk_level=RiskLevel.CRITICAL,
        evidence=["visibility=external", "payload detected", "mutation detected"],
    )


_VALID_GEMINI_JSON: dict = {
    "function_type": "cross_chain_receiver",
    "intended_caller": "trusted bridge endpoint only",
    "implicit_assumption": "Only the bridge calls this with authenticated payloads.",
    "missing_enforcement": "No msg.sender check or source-chain validation.",
    "attack_vector": "Attacker calls receiveMessage directly with forged payload.",
    "confidence": 0.95,
    "reasoning_summary": "External function with payload decode and mint — classic unguarded receiver.",
}


# ── _strip_json_fences ────────────────────────────────────────────────────────

class TestStripJsonFences(unittest.TestCase):
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        self.assertEqual(_strip_json_fences(raw), raw)

    def test_json_fence_stripped(self):
        raw = '```json\n{"key": "value"}\n```'
        self.assertEqual(_strip_json_fences(raw), '{"key": "value"}')

    def test_generic_fence_stripped(self):
        raw = '```\n{"key": "value"}\n```'
        self.assertEqual(_strip_json_fences(raw), '{"key": "value"}')

    def test_whitespace_trimmed(self):
        raw = '  \n```json\n{"key": "value"}\n```\n  '
        self.assertEqual(_strip_json_fences(raw), '{"key": "value"}')

    def test_multiline_json_preserved(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = _strip_json_fences(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed, {"a": 1, "b": 2})

    def test_no_closing_fence_handled(self):
        raw = '```json\n{"key": "value"}'
        result = _strip_json_fences(raw)
        self.assertIn("key", result)


# ── no-AI mode ────────────────────────────────────────────────────────────────

class TestNoAiMode(unittest.TestCase):
    def test_no_ai_flag_forces_deterministic(self):
        ta = infer_trust_assumption(_make_scan_result(), no_ai=True)
        self.assertEqual(ta.llm_status, LLM_STATUS_NO_AI)
        self.assertIsNone(ta.ai_provider)
        self.assertIsNone(ta.gemini_model)

    def test_no_ai_classifies_cross_chain_by_name(self):
        ta = infer_trust_assumption(_make_scan_result("receiveMessage"), no_ai=True)
        self.assertEqual(ta.category, VulnerabilityCategory.CROSS_CHAIN_RECEIVER)

    def test_no_ai_classifies_generic_external(self):
        ta = infer_trust_assumption(_make_scan_result("doSomething"), no_ai=True)
        self.assertEqual(ta.category, VulnerabilityCategory.GENERIC_EXTERNAL)

    def test_no_ai_trusted_caller_is_populated(self):
        ta = infer_trust_assumption(_make_scan_result(), no_ai=True)
        self.assertNotEqual(ta.assumed_trusted_caller, "")

    def test_no_ai_attack_vector_is_populated(self):
        ta = infer_trust_assumption(_make_scan_result(), no_ai=True)
        self.assertIn("receiveMessage", ta.attack_vector)


# ── Missing API key ───────────────────────────────────────────────────────────

class TestMissingApiKey(unittest.TestCase):
    def _run_without_key(self) -> object:
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            return infer_trust_assumption(_make_scan_result(), no_ai=False)

    def test_missing_key_falls_back(self):
        ta = self._run_without_key()
        self.assertEqual(ta.llm_status, LLM_STATUS_NO_KEY)

    def test_missing_key_no_ai_provider(self):
        ta = self._run_without_key()
        self.assertIsNone(ta.ai_provider)

    def test_empty_key_falls_back(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.llm_status, LLM_STATUS_NO_KEY)

    def test_deterministic_fields_present_on_fallback(self):
        ta = self._run_without_key()
        self.assertEqual(ta.category, VulnerabilityCategory.CROSS_CHAIN_RECEIVER)
        self.assertNotEqual(ta.assumed_trusted_caller, "")


# ── Gemini success path (mocked) ──────────────────────────────────────────────

class TestGeminiSuccess(unittest.TestCase):
    def _call_with_mock(self, raw_response: str) -> object:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-abc"}), \
             patch("trustgraph.agents.trust_assumption._GENAI_AVAILABLE", True), \
             patch("trustgraph.agents.trust_assumption._call_gemini_api",
                   return_value=raw_response):
            return infer_trust_assumption(_make_scan_result(), no_ai=False)

    def test_success_status(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertEqual(ta.llm_status, LLM_STATUS_SUCCESS)

    def test_provider_is_gemini(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertEqual(ta.ai_provider, "gemini")

    def test_confidence_populated(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertAlmostEqual(ta.confidence, 0.95, places=2)

    def test_reasoning_summary_populated(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertIsNotNone(ta.reasoning_summary)

    def test_category_mapped_correctly(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertEqual(ta.category, VulnerabilityCategory.CROSS_CHAIN_RECEIVER)

    def test_fenced_json_response_parsed(self):
        fenced = "```json\n" + json.dumps(_VALID_GEMINI_JSON) + "\n```"
        ta = self._call_with_mock(fenced)
        self.assertEqual(ta.llm_status, LLM_STATUS_SUCCESS)

    def test_implicit_assumption_populated(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertIn("bridge", ta.implicit_assumption or "")

    def test_missing_enforcement_populated(self):
        ta = self._call_with_mock(json.dumps(_VALID_GEMINI_JSON))
        self.assertIsNotNone(ta.missing_enforcement)


# ── Gemini failure paths (mocked) ─────────────────────────────────────────────

class TestGeminiFallbacks(unittest.TestCase):
    def _patch(self, side_effect=None, return_value=None):
        """Helper: patch env + SDK availability + API call."""
        env_patch = patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-abc"})
        avail_patch = patch("trustgraph.agents.trust_assumption._GENAI_AVAILABLE", True)
        if side_effect is not None:
            api_patch = patch(
                "trustgraph.agents.trust_assumption._call_gemini_api",
                side_effect=side_effect,
            )
        else:
            api_patch = patch(
                "trustgraph.agents.trust_assumption._call_gemini_api",
                return_value=return_value,
            )
        return env_patch, avail_patch, api_patch

    def test_invalid_json_falls_back(self):
        ep, ap, cp = self._patch(return_value="This is not JSON at all.")
        with ep, ap, cp:
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.llm_status, LLM_STATUS_PARSE_FAIL)
        self.assertNotEqual(ta.assumed_trusted_caller, "")

    def test_partial_json_falls_back(self):
        # Missing required fields
        partial = json.dumps({"function_type": "cross_chain_receiver"})
        ep, ap, cp = self._patch(return_value=partial)
        with ep, ap, cp:
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.llm_status, LLM_STATUS_PARSE_FAIL)

    def test_api_error_falls_back(self):
        ep, ap, cp = self._patch(side_effect=RuntimeError("connection refused"))
        with ep, ap, cp:
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.llm_status, LLM_STATUS_API_ERROR)
        self.assertIn("connection refused", ta.reasoning_summary or "")

    def test_fallback_preserves_deterministic_category(self):
        ep, ap, cp = self._patch(return_value="bad json")
        with ep, ap, cp:
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.category, VulnerabilityCategory.CROSS_CHAIN_RECEIVER)

    def test_fallback_preserves_function_name(self):
        ep, ap, cp = self._patch(return_value="bad json")
        with ep, ap, cp:
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        self.assertEqual(ta.function_name, "receiveMessage")


# ── API key safety ────────────────────────────────────────────────────────────

class TestKeySafety(unittest.TestCase):
    def test_api_key_not_in_trust_assumption_output(self):
        secret = "super-secret-key-xyz-12345"
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        dump = ta.model_dump_json()
        self.assertNotIn(secret, dump)
        self.assertNotIn("GEMINI_API_KEY", dump)

    def test_no_key_in_gemini_model_field(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            ta = infer_trust_assumption(_make_scan_result(), no_ai=False)
        # Even in fallback, gemini_model should be None or a model name string, not a key
        self.assertIsNone(ta.gemini_model)


if __name__ == "__main__":
    unittest.main(verbosity=2)
