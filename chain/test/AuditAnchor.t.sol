// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AuditAnchor} from "../src/AuditAnchor.sol";

interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function warp(uint256) external;
}

contract AuditAnchorTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    AuditAnchor a;
    bytes32 constant CHAIN = keccak256("journal:run-1");

    function setUp() public {
        a = new AuditAnchor();
    }

    function test_owner_anchors_and_reads_back() public {
        vm.warp(1_790_000_000);
        a.anchor(CHAIN, 10, bytes32(uint256(1)), bytes32(uint256(2)));
        a.anchor(CHAIN, 25, bytes32(uint256(3)), bytes32(uint256(4)));
        require(a.count(CHAIN) == 2, "count");
        AuditAnchor.Anchor memory l = a.latest(CHAIN);
        require(l.seq == 25 && l.merkleRoot == bytes32(uint256(3)) && l.at == 1_790_000_000, "latest");
        require(a.get(CHAIN, 0).headHash == bytes32(uint256(2)), "first");
    }

    function test_only_owner_can_anchor() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert(abi.encodeWithSelector(AuditAnchor.NotOwner.selector));
        a.anchor(CHAIN, 1, bytes32(0), bytes32(0));
    }

    function test_sequence_must_grow() public {
        a.anchor(CHAIN, 10, bytes32(uint256(1)), bytes32(uint256(2)));
        vm.expectRevert(abi.encodeWithSelector(AuditAnchor.NotNewer.selector, uint64(10), uint64(10)));
        a.anchor(CHAIN, 10, bytes32(uint256(9)), bytes32(uint256(9)));
    }

    function test_chains_are_independent() public {
        a.anchor(CHAIN, 10, bytes32(uint256(1)), bytes32(uint256(2)));
        a.anchor(keccak256("journal:run-2"), 3, bytes32(uint256(5)), bytes32(uint256(6)));
        require(a.count(CHAIN) == 1, "independent");
    }

    function test_latest_without_anchor_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(AuditAnchor.NoAnchor.selector));
        a.latest(CHAIN);
    }
}
