// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./MockToken.sol";

/// @notice Demonstrates the CrossCurve-style vulnerability:
/// a cross-chain receiver function with no caller guard.
///
/// The INTENDED model: only the bridge calls receiveMessage.
/// The ACTUAL model:   anyone can call receiveMessage.
///
/// Vulnerability predicate:
///   E(f)  — external visibility
///   P(f)  — bytes calldata payload (attacker-controlled)
///   V(f)  — token.mint() — critical state mutation
///   !G(f) — no msg.sender check or trusted-caller guard
contract VulnerableReceiver {
    MockToken public token;

    event MessageReceived(address indexed to, uint256 amount);

    constructor(address _token) {
        token = MockToken(_token);
    }

    /// @dev VULNERABLE: any external caller can forge a payload and mint tokens.
    function receiveMessage(bytes calldata payload) external {
        (address to, uint256 amount) = abi.decode(payload, (address, uint256));
        token.mint(to, amount);
        emit MessageReceived(to, amount);
    }
}
