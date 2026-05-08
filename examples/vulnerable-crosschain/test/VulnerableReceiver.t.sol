// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/MockToken.sol";
import "../src/VulnerableReceiver.sol";
import "../src/SecureReceiver.sol";

/// @notice Pre-written exploit test for the vulnerable-crosschain example.
/// Demonstrates that VulnerableReceiver can be directly invoked by any attacker,
/// while SecureReceiver correctly reverts on untrusted calls.
contract VulnerableReceiverExploitTest is Test {
    MockToken token;
    VulnerableReceiver vulnReceiver;
    SecureReceiver secureReceiver;
    address bridge = makeAddr("bridge");
    address attacker = makeAddr("attacker");

    function setUp() public {
        token = new MockToken();
        vulnReceiver = new VulnerableReceiver(address(token));

        // SecureReceiver only accepts calls from `bridge`
        secureReceiver = new SecureReceiver(address(token), bridge);
    }

    // ── Exploit tests ─────────────────────────────────────────────────────────

    /// Prove the core vulnerability: attacker mints tokens without bridge involvement.
    function test_attackerMintsViaVulnerableReceiver() public {
        assertEq(token.balanceOf(attacker), 0);

        vm.startPrank(attacker);
        bytes memory payload = abi.encode(attacker, 1000 ether);
        vulnReceiver.receiveMessage(payload);
        vm.stopPrank();

        assertGt(token.balanceOf(attacker), 0, "Exploit failed: no tokens minted");
        assertEq(token.balanceOf(attacker), 1000 ether, "Wrong amount minted");
    }

    /// Attacker can mint to any arbitrary address.
    function test_attackerMintsToArbitraryRecipient() public {
        address victim = makeAddr("victim");

        vm.prank(attacker);
        bytes memory payload = abi.encode(victim, 500 ether);
        vulnReceiver.receiveMessage(payload);

        assertEq(token.balanceOf(victim), 500 ether);
    }

    // ── Secure-path tests ─────────────────────────────────────────────────────

    /// Attacker cannot call SecureReceiver directly — reverts as expected.
    function test_attackerCannotCallSecureReceiver() public {
        vm.startPrank(attacker);
        bytes memory payload = abi.encode(attacker, 1000 ether);
        vm.expectRevert("SecureReceiver: untrusted caller");
        secureReceiver.receiveMessage(payload);
        vm.stopPrank();
    }

    /// The legitimate bridge CAN call SecureReceiver.
    function test_bridgeCanCallSecureReceiver() public {
        vm.startPrank(bridge);
        bytes memory payload = abi.encode(attacker, 1000 ether);
        secureReceiver.receiveMessage(payload);
        vm.stopPrank();

        assertEq(token.balanceOf(attacker), 1000 ether, "Bridge call failed unexpectedly");
    }
}
