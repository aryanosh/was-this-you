const hre = require("hardhat");

async function main() {
  const FingerprintRegistry = await hre.ethers.getContractFactory("FingerprintRegistry");
  const registry = await FingerprintRegistry.deploy();
  await registry.waitForDeployment();

  const address = await registry.getAddress();
  const deployTx = registry.deploymentTransaction();

  console.log("FingerprintRegistry deployed to:", address);
  console.log("Deployment tx hash:", deployTx.hash);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
