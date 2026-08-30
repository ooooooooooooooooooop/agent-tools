import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const artifactPath = process.env.DSH_ARCHIVE_ARTIFACT;
const sidecarPath = process.env.DSH_ARCHIVE_SIDECAR;
if (!artifactPath || !sidecarPath) {
	console.error("DSH_ARCHIVE_ARTIFACT and DSH_ARCHIVE_SIDECAR are required");
	process.exit(2);
}

const [artifact, sidecar] = await Promise.all([
	readFile(artifactPath),
	readFile(sidecarPath, "utf8").then((body) => JSON.parse(body))
]);
const observedSha256 = createHash("sha256").update(artifact).digest("hex");
const expectedBytes = process.env.DSH_ARCHIVE_EXPECTED_BYTES === undefined
	? undefined
	: Number(process.env.DSH_ARCHIVE_EXPECTED_BYTES);
const expectedSha256 = process.env.DSH_ARCHIVE_EXPECTED_SHA256;
const sidecarSha256 = sidecar.evidence?.artifactSha256;
const contentUnchanged = sidecar.evidence?.contentUnchanged === true
	&& observedSha256 === sidecarSha256
	&& (expectedSha256 === undefined || observedSha256 === expectedSha256)
	&& (expectedBytes === undefined || artifact.byteLength === expectedBytes);

const evidence = {
	kind: "ARCHIVE_INTEGRITY_EVIDENCE_V1",
	artifactPath,
	observedBytes: artifact.byteLength,
	observedSha256,
	expectedBytes,
	expectedSha256,
	sidecarSessionId: sidecar.sessionId,
	sidecarStatus: sidecar.status,
	sidecarOperationalLabel: sidecar.operationalLabel,
	sidecarSha256,
	contentUnchanged
};
console.log(JSON.stringify(evidence, null, 2));
if (!contentUnchanged) process.exit(1);
