from __future__ import annotations

from trustgraph.models import (
    Finding,
    PatchRecommendation,
    VulnerabilityCategory,
)


def _cross_chain_patch(func_name: str, params: str) -> PatchRecommendation:
    return PatchRecommendation(
        description=(
            "Add an explicit caller-validation guard. Store the trusted bridge/endpoint "
            "address at construction time and require msg.sender to match before processing "
            "any payload."
        ),
        code_snippet=f"""\
address public immutable trustedBridge;

constructor(address _trustedBridge, /* other args */) {{
    trustedBridge = _trustedBridge;
}}

function {func_name}({params}) external {{
    require(msg.sender == trustedBridge, "Untrusted caller");
    // ... existing logic ...
}}
""",
    )


def _generic_external_patch(func_name: str, params: str) -> PatchRecommendation:
    return PatchRecommendation(
        description=(
            "Restrict the function to an authorised caller using OpenZeppelin's Ownable "
            "or AccessControl, or add an explicit require check against a stored address."
        ),
        code_snippet=f"""\
// Option A — role-based (recommended for flexibility)
import "@openzeppelin/contracts/access/AccessControl.sol";

bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");

function {func_name}({params}) external onlyRole(OPERATOR_ROLE) {{
    // ... existing logic ...
}}

// Option B — single trusted address
address public operator;

function {func_name}({params}) external {{
    require(msg.sender == operator, "Not authorised");
    // ... existing logic ...
}}
""",
    )


def recommend_patch(finding: Finding) -> PatchRecommendation:
    func_name = finding.scan_result.function_info.name
    params = finding.scan_result.function_info.params
    category = finding.trust_assumption.category

    if category == VulnerabilityCategory.CROSS_CHAIN_RECEIVER:
        return _cross_chain_patch(func_name, params)

    return _generic_external_patch(func_name, params)
