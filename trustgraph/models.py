from __future__ import annotations

from enum import Enum
from typing import Optional, TypedDict

from pydantic import BaseModel


class RiskLevel(str, Enum):
    CRITICAL = "Critical"
    MEDIUM = "Medium"
    INFORMATIONAL = "Informational"


class VulnerabilityCategory(str, Enum):
    CROSS_CHAIN_RECEIVER = "cross_chain_receiver"
    GENERIC_EXTERNAL = "generic_external"
    UNKNOWN = "unknown"


class FunctionInfo(BaseModel):
    file: str
    line: int
    name: str
    visibility: str  # public | external
    params: str
    modifiers: list[str]
    body: str


class ScoreBreakdown(BaseModel):
    exposure: bool   # public or external
    payload: bool    # bytes/calldata/abi.decode/payload/message/data param
    mutation: bool   # mint/transfer/withdraw/etc in body
    guard: bool      # access control found in body or modifiers


class ScanResult(BaseModel):
    function_info: FunctionInfo
    scores: ScoreBreakdown
    risk_level: RiskLevel
    evidence: list[str]


class GeminiClassification(BaseModel):
    """Schema for the structured JSON Gemini returns."""
    function_type: str
    intended_caller: str
    implicit_assumption: str
    missing_enforcement: str
    attack_vector: str
    confidence: float
    reasoning_summary: str


class TrustAssumption(BaseModel):
    function_name: str
    category: VulnerabilityCategory
    assumed_trusted_caller: str
    attack_vector: str
    # AI metadata — populated only when Gemini is called
    llm_status: str = "no_ai_forced"          # see LLM_STATUS_* constants in trust_assumption.py
    llm_display_message: Optional[str] = None  # clean human-readable status for UI
    ai_provider: Optional[str] = None
    gemini_model: Optional[str] = None
    confidence: Optional[float] = None
    reasoning_summary: Optional[str] = None   # raw AI summary (success) or raw error blob (fallback)
    implicit_assumption: Optional[str] = None
    missing_enforcement: Optional[str] = None


class FoundryResult(BaseModel):
    ran: bool
    passed: Optional[bool] = None
    output: str
    test_path: str


class PatchRecommendation(BaseModel):
    description: str
    code_snippet: str


class Finding(BaseModel):
    scan_result: ScanResult
    trust_assumption: TrustAssumption
    exploit_path: Optional[str] = None
    foundry_result: Optional[FoundryResult] = None
    patch: Optional[PatchRecommendation] = None


# TypedDict used as LangGraph state — findings accumulate as dict keys are added across nodes.
class WorkflowState(TypedDict):
    solidity_files: list[str]
    raw_functions: list[dict]
    findings: list[dict]
    report_paths: list[str]
    config: dict
    errors: list[str]
