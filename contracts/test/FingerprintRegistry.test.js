const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("FingerprintRegistry", function () {
  async function deployFixture() {
    const [owner, other] = await ethers.getSigners();
    const FingerprintRegistry = await ethers.getContractFactory("FingerprintRegistry");
    const registry = await FingerprintRegistry.deploy();
    await registry.waitForDeployment();
    return { registry, owner, other };
  }

  const sampleHash = ethers.keccak256(ethers.toUtf8Bytes("sample fingerprint blob"));
  const sourceUrl = "https://example.com/post/123";

  it("registers a new fingerprint and emits an event", async function () {
    const { registry, owner } = await deployFixture();

    await expect(registry.registerFingerprint(sampleHash, sourceUrl))
      .to.emit(registry, "FingerprintRegistered")
      .withArgs(sampleHash, owner.address, anyUint(), sourceUrl);

    const [timestamp, submitter, storedUrl] = await registry.getRecord(sampleHash);
    expect(submitter).to.equal(owner.address);
    expect(storedUrl).to.equal(sourceUrl);
    expect(timestamp).to.be.greaterThan(0);
  });

  it("reverts when registering the same hash twice", async function () {
    const { registry } = await deployFixture();
    await registry.registerFingerprint(sampleHash, sourceUrl);
    await expect(registry.registerFingerprint(sampleHash, sourceUrl)).to.be.revertedWithCustomError(
      registry,
      "AlreadyRegistered"
    );
  });

  it("reverts reading a hash that was never registered", async function () {
    const { registry } = await deployFixture();
    await expect(registry.getRecord(sampleHash)).to.be.revertedWithCustomError(registry, "NotRegistered");
  });

  it("isRegistered reflects state without reverting", async function () {
    const { registry } = await deployFixture();
    expect(await registry.isRegistered(sampleHash)).to.equal(false);
    await registry.registerFingerprint(sampleHash, sourceUrl);
    expect(await registry.isRegistered(sampleHash)).to.equal(true);
  });

  it("stores independent records per submitter address", async function () {
    const { registry, other } = await deployFixture();
    const otherHash = ethers.keccak256(ethers.toUtf8Bytes("a different blob"));
    await registry.connect(other).registerFingerprint(otherHash, sourceUrl);
    const [, submitter] = await registry.getRecord(otherHash);
    expect(submitter).to.equal(other.address);
  });
});

// chai-matchers' anyValue helper for the timestamp arg in the event assertion
function anyUint() {
  const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
  return anyValue;
}
