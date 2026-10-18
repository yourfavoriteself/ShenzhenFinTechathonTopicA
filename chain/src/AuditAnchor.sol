// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AuditAnchor
/// @notice Public anchor for the Guardian kernel journal. Only hashes are stored: the Merkle root of the
///         journal entries and the hash of the newest entry at a sequence number. No client data, amounts
///         or payees ever reach the chain. Anyone holding a copy of the journal can recompute the root and
///         compare; a rewritten or truncated journal no longer matches the anchor.
contract AuditAnchor {
    struct Anchor {
        bytes32 merkleRoot;
        bytes32 headHash;
        uint64 seq;
        uint64 at;
    }

    address public immutable owner;
    mapping(bytes32 => Anchor[]) private chains;

    event Anchored(bytes32 indexed chain, uint64 seq, bytes32 merkleRoot, bytes32 headHash);

    error NotOwner();
    error NotNewer(uint64 last, uint64 seq);
    error NoAnchor();

    constructor() {
        owner = msg.sender;
    }

    /// @param chain identifier of one kernel run's hash chain (hash of the journal key and run id)
    /// @param seq sequence number of the newest journal entry covered
    function anchor(bytes32 chain, uint64 seq, bytes32 merkleRoot, bytes32 headHash) external {
        if (msg.sender != owner) revert NotOwner();
        Anchor[] storage a = chains[chain];
        if (a.length > 0 && seq <= a[a.length - 1].seq) revert NotNewer(a[a.length - 1].seq, seq);
        a.push(Anchor(merkleRoot, headHash, seq, uint64(block.timestamp)));
        emit Anchored(chain, seq, merkleRoot, headHash);
    }

    function count(bytes32 chain) external view returns (uint256) {
        return chains[chain].length;
    }

    function get(bytes32 chain, uint256 i) external view returns (Anchor memory) {
        return chains[chain][i];
    }

    function latest(bytes32 chain) external view returns (Anchor memory) {
        Anchor[] storage a = chains[chain];
        if (a.length == 0) revert NoAnchor();
        return a[a.length - 1];
    }
}
