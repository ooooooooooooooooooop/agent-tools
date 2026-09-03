// Pure decision controller for target-aware model switching.
//
// This module is deliberately free of host, cordis, session, and I/O state:
// every input arrives as a plain object and the output is a plain object.
// The adapter layer is responsible for measuring, resolving capacity truth,
// and executing the chosen mode. The controller NEVER guesses capacity and
// NEVER weakens the admission inequality.

export const SWITCH_MODES = Object.freeze({
  IN_PLACE: "IN_PLACE_SWITCH",
  COMPACT_THEN_SWITCH: "COMPACT_THEN_SWITCH",
  HANDOFF: "HANDOFF_SWITCH",
  BLOCKED: "BLOCKED_WITH_REASON",
});

export const CAPACITY_PROVENANCE = Object.freeze({
  PROVIDER_ATTESTED: "PROVIDER_ATTESTED",
  PROVIDER_DISCOVERED: "PROVIDER_DISCOVERED",
  CANONICAL_VERIFIED: "CANONICAL_VERIFIED",
  CONSERVATIVE_FALLBACK: "CONSERVATIVE_FALLBACK",
});

export const BLOCK_REASONS = Object.freeze({
  TARGET_MODEL_UNAVAILABLE: "TARGET_MODEL_UNAVAILABLE",
  INVALID_INPUT: "INVALID_INPUT",
});

export const HANDOFF_REASONS = Object.freeze({
  COMPACTION_NOT_USEFUL: "COMPACTION_NOT_USEFUL",
  COMPACTION_EXHAUSTED: "COMPACTION_EXHAUSTED",
  COMPACTION_UNAVAILABLE: "COMPACTION_UNAVAILABLE",
  COMPACTION_FAILED: "COMPACTION_FAILED",
  OVER_LIMIT_NO_COMPACTION: "OVER_LIMIT_NO_COMPACTION",
});

export const COMPACT_REASONS = Object.freeze({
  OVER_TARGET_CAPACITY: "OVER_TARGET_CAPACITY",
});

// Conservative admitted limit for UNKNOWN capacity. This is NOT a claim about
// the provider's real context window; it is the largest request we are willing
// to admit when capacity truth is unproven. See CAPACITY_PROVENANCE.
export const DEFAULT_CONSERVATIVE_FALLBACK_LIMIT = 262144;

export function conservativeFallbackLimit(declaredContext, fallback = DEFAULT_CONSERVATIVE_FALLBACK_LIMIT) {
  if (!Number.isSafeInteger(declaredContext) || declaredContext <= 0) return fallback;
  return Math.min(declaredContext, fallback);
}

// Effective limit + provenance, given declared context and optional attested
// evidence. The caller (adapter) owns evidence collection; here we only
// combine what we are given. Attestation beats declaration; unknown capacity
// degrades to the conservative admitted limit, never to the declared number.
export function resolveEffectiveLimit({ declaredContext, attestedLimit, capacityProvenance }) {
  if (Number.isSafeInteger(attestedLimit) && attestedLimit > 0) {
    if (Number.isSafeInteger(declaredContext) && declaredContext > 0) {
      return {
        effectiveLimit: Math.min(declaredContext, attestedLimit),
        capacityProvenance: capacityProvenance ?? CAPACITY_PROVENANCE.PROVIDER_ATTESTED,
      };
    }
    return {
      effectiveLimit: attestedLimit,
      capacityProvenance: capacityProvenance ?? CAPACITY_PROVENANCE.PROVIDER_ATTESTED,
    };
  }
  if (Number.isSafeInteger(declaredContext) && declaredContext > 0) {
    return {
      effectiveLimit: conservativeFallbackLimit(declaredContext),
      capacityProvenance: CAPACITY_PROVENANCE.CONSERVATIVE_FALLBACK,
    };
  }
  return {
    effectiveLimit: DEFAULT_CONSERVATIVE_FALLBACK_LIMIT,
    capacityProvenance: CAPACITY_PROVENANCE.CONSERVATIVE_FALLBACK,
  };
}

function isNonNegInt(v) {
  return Number.isSafeInteger(v) && v >= 0;
}

// Decide the switch mode. Inputs are the measured/resolved truth assembled by
// the adapter. The admission inequality uses the EFFECTIVE limit only:
//
//   input + safetyMargin + requiredOutputBudget <= targetEffectiveLimit
//
// where `input` is post-compaction input once a compaction has run.
export function decideModelSwitch({
  sourceSessionId,
  currentModel,
  targetProvider,
  targetModel,
  conservativeInput,
  safetyMargin,
  requiredOutputBudget,
  targetDeclaredContext,
  targetEffectiveLimit,
  targetCapacityProvenance,
  targetRuntimeAdmitted = true,
  compactionAvailable = true,
  compactionAttemptsUsed = 0,
  maxCompactionAttempts = 2,
  lastCompactionResult = null, // null | "ok" | "not_useful" | "error" | "no_region"
  postCompactionInput = null,
}) {
  const evidence = {
    sourceSessionId: sourceSessionId ?? null,
    currentModel: currentModel ?? null,
    targetProvider: targetProvider ?? null,
    targetModel: targetModel ?? null,
    conservativeInput,
    safetyMargin,
    requiredOutputBudget,
    targetDeclaredContext,
    targetEffectiveLimit,
    targetCapacityProvenance,
    compactionAttemptsUsed,
    maxCompactionAttempts,
    lastCompactionResult,
  };

  if (!targetRuntimeAdmitted) {
    return {
      mode: SWITCH_MODES.BLOCKED,
      reason: BLOCK_REASONS.TARGET_MODEL_UNAVAILABLE,
      evidence,
    };
  }
  for (const [key, value] of [
    ["conservativeInput", conservativeInput],
    ["safetyMargin", safetyMargin],
    ["requiredOutputBudget", requiredOutputBudget],
    ["targetEffectiveLimit", targetEffectiveLimit],
  ]) {
    if (!isNonNegInt(value)) {
      return {
        mode: SWITCH_MODES.BLOCKED,
        reason: BLOCK_REASONS.INVALID_INPUT,
        evidence: { ...evidence, invalidField: key },
      };
    }
  }

  const input = lastCompactionResult && lastCompactionResult !== "error" && isNonNegInt(postCompactionInput)
    ? postCompactionInput
    : conservativeInput;
  evidence.admittedInput = input;
  const fits = input + safetyMargin + requiredOutputBudget <= targetEffectiveLimit;
  evidence.fits = fits;

  if (fits) {
    if (lastCompactionResult === "ok") {
      return { mode: SWITCH_MODES.COMPACT_THEN_SWITCH, reason: null, evidence };
    }
    return { mode: SWITCH_MODES.IN_PLACE, reason: null, evidence };
  }

  // Over the target effective limit.
  if (lastCompactionResult === "not_useful" || lastCompactionResult === "no_region") {
    return {
      mode: SWITCH_MODES.HANDOFF,
      reason: HANDOFF_REASONS.COMPACTION_NOT_USEFUL,
      evidence,
    };
  }
  if (lastCompactionResult === "error") {
    return { mode: SWITCH_MODES.HANDOFF, reason: HANDOFF_REASONS.COMPACTION_FAILED, evidence };
  }
  if (compactionAttemptsUsed >= maxCompactionAttempts) {
    return {
      mode: SWITCH_MODES.HANDOFF,
      reason: HANDOFF_REASONS.COMPACTION_EXHAUSTED,
      evidence,
    };
  }
  if (!compactionAvailable) {
    return {
      mode: SWITCH_MODES.HANDOFF,
      reason: HANDOFF_REASONS.COMPACTION_UNAVAILABLE,
      evidence,
    };
  }
  return {
    mode: SWITCH_MODES.COMPACT_THEN_SWITCH,
    reason: COMPACT_REASONS.OVER_TARGET_CAPACITY,
    evidence,
  };
}
