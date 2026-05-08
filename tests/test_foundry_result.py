"""
Tests for Foundry result plumbing in the workflow graph.

Verifies that foundry_result.test_path is set to the finding's own exploit_path
rather than being left as an empty string.
"""
from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from trustgraph.graph import run_foundry_tests
from trustgraph.models import RiskLevel


def _base_state(findings: list[dict], path: str = "/fake/src") -> dict:
    return {
        "solidity_files": [],
        "raw_functions": [],
        "findings": findings,
        "report_paths": [],
        "config": {"path": path, "run_foundry": True},
        "errors": [],
    }


def _critical_finding(exploit_path: str | None = "/out/ExploitTest.t.sol") -> dict:
    return {
        "risk_level": RiskLevel.CRITICAL.value,
        "scan_result": {
            "function_info": {
                "file": "Vulnerable.sol", "line": 10, "name": "receiveMessage",
                "visibility": "external", "params": "bytes calldata payload",
                "modifiers": [], "body": "token.mint(to, amount);",
            },
            "scores": {"exposure": True, "payload": True, "mutation": True, "guard": False},
            "risk_level": RiskLevel.CRITICAL.value,
            "evidence": [],
        },
        "trust_assumption": {
            "function_name": "receiveMessage",
            "category": "cross_chain_receiver",
            "assumed_trusted_caller": "bridge",
            "attack_vector": "direct call",
            "llm_status": "no_ai_forced",
        },
        "exploit_path": exploit_path,
        "foundry_result": None,
        "patch": None,
    }


def _medium_finding() -> dict:
    fd = _critical_finding(exploit_path=None)
    fd["risk_level"] = RiskLevel.MEDIUM.value
    fd["scan_result"]["risk_level"] = RiskLevel.MEDIUM.value
    return fd


class TestFoundryTestPath(unittest.TestCase):

    def _run_with_mock_forge(self, findings: list[dict], foundry_root: str = "/fake/foundry"):
        """Run run_foundry_tests with forge mocked to succeed."""
        from trustgraph.models import FoundryResult

        mock_result = FoundryResult(ran=True, passed=True, output="[PASS]", test_path="")

        with patch("trustgraph.graph.find_foundry_root", return_value=foundry_root), \
             patch("trustgraph.graph.run_forge_test", return_value=mock_result):
            state = _base_state(findings)
            return run_foundry_tests(state)

    # ── Critical finding with exploit_path ────────────────────────────────────

    def test_test_path_matches_exploit_path(self):
        exploit_path = "/out/tests/VulnerableExploit.t.sol"
        result = self._run_with_mock_forge([_critical_finding(exploit_path)])
        fr = result["findings"][0]["foundry_result"]
        self.assertEqual(fr["test_path"], exploit_path)

    def test_test_path_not_empty_string(self):
        result = self._run_with_mock_forge([_critical_finding("/out/Exploit.t.sol")])
        fr = result["findings"][0]["foundry_result"]
        self.assertNotEqual(fr["test_path"], "")

    def test_ran_and_passed_preserved(self):
        result = self._run_with_mock_forge([_critical_finding()])
        fr = result["findings"][0]["foundry_result"]
        self.assertTrue(fr["ran"])
        self.assertTrue(fr["passed"])

    # ── Critical finding without exploit_path ─────────────────────────────────

    def test_critical_no_exploit_path_gets_empty_test_path(self):
        """Critical finding with no generated test: test_path should be ''."""
        result = self._run_with_mock_forge([_critical_finding(exploit_path=None)])
        fr = result["findings"][0]["foundry_result"]
        self.assertEqual(fr["test_path"], "")

    # ── Multiple findings: each gets its own test_path ────────────────────────

    def test_each_finding_gets_own_test_path(self):
        path_a = "/out/ContractA.t.sol"
        path_b = "/out/ContractB.t.sol"
        fd_a = _critical_finding(path_a)
        fd_b = _critical_finding(path_b)
        result = self._run_with_mock_forge([fd_a, fd_b])
        self.assertEqual(result["findings"][0]["foundry_result"]["test_path"], path_a)
        self.assertEqual(result["findings"][1]["foundry_result"]["test_path"], path_b)

    def test_shared_run_result_not_mutated(self):
        """The single run_forge_test result dict should not be mutated in-place."""
        path_a = "/out/A.t.sol"
        path_b = "/out/B.t.sol"
        result = self._run_with_mock_forge([_critical_finding(path_a), _critical_finding(path_b)])
        # If the dict were mutated, both would end up with the same (last) path
        self.assertNotEqual(
            result["findings"][0]["foundry_result"]["test_path"],
            result["findings"][1]["foundry_result"]["test_path"],
        )

    # ── Medium finding: no foundry_result assigned ────────────────────────────

    def test_medium_finding_gets_no_foundry_result(self):
        result = self._run_with_mock_forge([_medium_finding()])
        self.assertIsNone(result["findings"][0].get("foundry_result"))

    # ── No foundry root found ─────────────────────────────────────────────────

    def test_no_foundry_root_sets_test_path_to_exploit_path(self):
        exploit_path = "/out/Exploit.t.sol"
        with patch("trustgraph.graph.find_foundry_root", return_value=None):
            state = _base_state([_critical_finding(exploit_path)])
            result = run_foundry_tests(state)
        fr = result["findings"][0]["foundry_result"]
        self.assertFalse(fr["ran"])
        self.assertEqual(fr["test_path"], exploit_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
