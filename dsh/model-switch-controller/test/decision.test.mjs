// decision.test.mjs — pure controller unit tests (spec §37 Cases A-F + §44-47 regressions).
// The §7 fixture (cpa/gemini-3.7-flash-high, effective 262144, provenance
// PROVIDER_ATTESTED_FIXTURE, conservative 424060) lives ONLY here — it is not
// baked into production config.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CAPACITY_PROVENANCE,
  DEFAULT_CONSERVATIVE_FALLBACK_LIMIT,
  decideModelSwitch,
  resolveEffectiveLimit,
} from "../lib/decision.js";

const M = 1024 * 1024;

function base(over = {}) {
  return {
    sourceSessionId: "s1",
    currentModel: "cpa/gpt-5.6-luna-max",
    targetProvider: "cpa",
    targetModel: "gemini-3.7-flash-high",
    conservativeInput: 100_000,
    safetyMargin: 16_384,
    requiredOutputBudget: 65_536,
    targetDeclaredContext: 1_048_576,
    targetEffectiveLimit: 262_144,
    targetCapacityProvenance: "PROVIDER_ATTESTED_FIXTURE",
    targetRuntimeAdmitted: true,
    compactionAvailable: true,
    compactionAttemptsUsed: 0,
    maxCompactionAttempts: 2,
    lastCompactionResult: null,
    postCompactionInput: null,
    ...over,
  };
}

test("Case A: 100k -> 262k fits => IN_PLACE_SWITCH", () => {
  const d = decideModelSwitch(base());
  assert.equal(d.mode, "IN_PLACE_SWITCH");
  assert.equal(d.reason, null);
  assert.equal(d.evidence.fits, true);
});

test("Case B: slightly over + compaction ok + fits after => COMPACT_THEN_SWITCH", () => {
  // 250k conservative: over? 250000+16384+65536 = 331920 > 262144 => over.
  const d = decideModelSwitch(base({
    conservativeInput: 250_000,
    lastCompactionResult: "ok",
    postCompactionInput: 170_000, // 170000+16384+65536 = 251920 <= 262144
  }));
  assert.equal(d.mode, "COMPACT_THEN_SWITCH");
  assert.equal(d.reason, null);
  assert.equal(d.evidence.admittedInput, 170_000);
});

test("Case C: 424060 -> 262144 + NOT_USEFUL => HANDOFF_SWITCH (COMPACTION_NOT_USEFUL)", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    lastCompactionResult: "not_useful",
    postCompactionInput: 424_060,
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_NOT_USEFUL");
  // Handoff is the endpoint — never CONTEXT_PREFLIGHT_BLOCKED (§44).
});

test("Case D: 424060 -> 262144, compaction ok but still over => HANDOFF_SWITCH (EXHAUSTED)", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    compactionAttemptsUsed: 2,
    maxCompactionAttempts: 2,
    lastCompactionResult: "ok",
    postCompactionInput: 300_000, // still over 262144
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_EXHAUSTED");
});

test("Case E: UNKNOWN capacity => conservative admitted limit, not declared window", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 300_000,
    targetDeclaredContext: 1_048_576,           // Gemini truth is UNKNOWN here
    targetEffectiveLimit: DEFAULT_CONSERVATIVE_FALLBACK_LIMIT,
    targetCapacityProvenance: CAPACITY_PROVENANCE.CONSERVATIVE_FALLBACK,
  }));
  assert.equal(d.mode, "COMPACT_THEN_SWITCH"); // 300k over 262144 -> compact path
  assert.notEqual(d.evidence.targetEffectiveLimit, 1_048_576);
  // resolveEffectiveLimit must never surface the declared window as truth.
  const r = resolveEffectiveLimit({ declaredContext: 1_048_576 });
  assert.equal(r.effectiveLimit, 262_144);
  assert.equal(r.capacityProvenance, CAPACITY_PROVENANCE.CONSERVATIVE_FALLBACK);
});

test("Case F: target not runtime admitted => BLOCKED TARGET_MODEL_UNAVAILABLE", () => {
  const d = decideModelSwitch(base({ targetRuntimeAdmitted: false }));
  assert.equal(d.mode, "BLOCKED_WITH_REASON");
  assert.equal(d.reason, "TARGET_MODEL_UNAVAILABLE");
});

test("§45: compaction always NOT_USEFUL is bounded => straight to HANDOFF, no infinite loop", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    lastCompactionResult: "not_useful",
    compactionAttemptsUsed: 1,
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_NOT_USEFUL");
});

test("§46: capacity change 262144 -> 1048576 PROVIDER_ATTESTED flips HANDOFF to IN_PLACE", () => {
  const over = { conservativeInput: 424_060 };
  const before = decideModelSwitch(base(over));
  assert.equal(before.mode, "COMPACT_THEN_SWITCH"); // over 262144, compact first
  const after = decideModelSwitch(base({
    ...over,
    targetEffectiveLimit: 1_048_576,
    targetCapacityProvenance: CAPACITY_PROVENANCE.PROVIDER_ATTESTED,
  }));
  assert.equal(after.mode, "IN_PLACE_SWITCH");
});

test("§14 invariant: IN_PLACE implies the guard inequality holds (no margin shrinking)", () => {
  // admitted input + safety + budget <= effectiveLimit is exactly the guard test
  const d = decideModelSwitch(base({ conservativeInput: 262_144 - 16_384 - 65_536 }));
  assert.equal(d.mode, "IN_PLACE_SWITCH");
  const edge = decideModelSwitch(base({ conservativeInput: 262_144 - 16_384 - 65_536 + 1 }));
  assert.notEqual(edge.mode, "IN_PLACE_SWITCH");
});

test("handoff when compaction unavailable and over limit", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    compactionAvailable: false,
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_UNAVAILABLE");
});

test("no compactable region maps to COMPACTION_NOT_USEFUL handoff", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    lastCompactionResult: "no_region",
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_NOT_USEFUL");
});

test("compaction error maps to COMPACTION_FAILED handoff", () => {
  const d = decideModelSwitch(base({
    conservativeInput: 424_060,
    lastCompactionResult: "error",
  }));
  assert.equal(d.mode, "HANDOFF_SWITCH");
  assert.equal(d.reason, "COMPACTION_FAILED");
});

test("attested limit caps declared window (PROVIDER_ATTESTED)", () => {
  const r = resolveEffectiveLimit({
    declaredContext: 1_048_576,
    attestedLimit: 500_000,
  });
  assert.equal(r.effectiveLimit, 500_000);
  assert.equal(r.capacityProvenance, CAPACITY_PROVENANCE.PROVIDER_ATTESTED);
});

test("invalid input blocks instead of guessing", () => {
  const d = decideModelSwitch(base({ conservativeInput: -1 }));
  assert.equal(d.mode, "BLOCKED_WITH_REASON");
  assert.equal(d.reason, "INVALID_INPUT");
  assert.equal(d.evidence.invalidField, "conservativeInput");
});
