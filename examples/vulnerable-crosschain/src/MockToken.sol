// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal mock token with open minting — represents the token contract
/// that the cross-chain receiver is authorised to mint on behalf of the bridge.
/// In production the minting authority is enforced at the RECEIVER layer, not here.
contract MockToken {
    string public constant name = "MockToken";
    string public constant symbol = "MTK";
    uint8 public constant decimals = 18;

    mapping(address => uint256) public balanceOf;
    uint256 public totalSupply;

    event Transfer(address indexed from, address indexed to, uint256 amount);

    /// @dev Open minting — the security model delegates access control to the caller.
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
