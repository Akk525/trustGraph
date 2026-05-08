// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./MockToken.sol";

/// @notice Secure version of the cross-chain receiver.
/// Enforces the trust assumption on-chain: only the trusted bridge may call receiveMessage.
contract SecureReceiver {
    MockToken public token;
    address public immutable trustedBridge;

    event MessageReceived(address indexed to, uint256 amount);

    constructor(address _token, address _trustedBridge) {
        token = MockToken(_token);
        trustedBridge = _trustedBridge;
    }

    /// @dev SECURE: msg.sender must be the trusted bridge.
    function receiveMessage(bytes calldata payload) external {
        require(msg.sender == trustedBridge, "SecureReceiver: untrusted caller");
        (address to, uint256 amount) = abi.decode(payload, (address, uint256));
        token.mint(to, amount);
        emit MessageReceived(to, amount);
    }
}
