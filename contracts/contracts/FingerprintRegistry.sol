// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal tamper-evident registry mapping a content fingerprint hash
/// to who submitted it, when, and where the source content was found.
contract FingerprintRegistry {
    struct Record {
        uint256 timestamp;
        address submitter;
        string sourceUrl;
    }

    mapping(bytes32 => Record) private records;

    event FingerprintRegistered(
        bytes32 indexed fingerprintHash,
        address indexed submitter,
        uint256 timestamp,
        string sourceUrl
    );

    error AlreadyRegistered(bytes32 fingerprintHash);
    error NotRegistered(bytes32 fingerprintHash);

    /// @notice Registers a fingerprint hash on-chain. Reverts if this exact
    /// hash was already registered, preserving first-write tamper-evidence.
    function registerFingerprint(bytes32 fingerprintHash, string calldata sourceUrl) external {
        if (records[fingerprintHash].timestamp != 0) {
            revert AlreadyRegistered(fingerprintHash);
        }

        records[fingerprintHash] = Record({
            timestamp: block.timestamp,
            submitter: msg.sender,
            sourceUrl: sourceUrl
        });

        emit FingerprintRegistered(fingerprintHash, msg.sender, block.timestamp, sourceUrl);
    }

    /// @notice Reads back a previously registered record. Reverts if nothing
    /// was ever registered under this hash.
    function getRecord(bytes32 fingerprintHash) external view returns (
        uint256 timestamp,
        address submitter,
        string memory sourceUrl
    ) {
        Record storage r = records[fingerprintHash];
        if (r.timestamp == 0) {
            revert NotRegistered(fingerprintHash);
        }
        return (r.timestamp, r.submitter, r.sourceUrl);
    }

    /// @notice Cheap existence check without reverting.
    function isRegistered(bytes32 fingerprintHash) external view returns (bool) {
        return records[fingerprintHash].timestamp != 0;
    }
}
