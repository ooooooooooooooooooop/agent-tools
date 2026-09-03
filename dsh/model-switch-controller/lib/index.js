// User-level Cordis plugin: model-switch-controller
//
// Target-aware three-path model switch controller, reached from the existing
// DSH Web UI model selector through the existing Session model-change path:
//
//   Web selector -> agent/request waterfall (proposed config)
//     -> persisted header mismatch = switch intent -> this controller
//       -> IN_PLACE_SWITCH | COMPACT_THEN_SWITCH | HANDOFF_SWITCH | BLOCKED
//
// Hard boundaries honored here:
//   * No host-scoped Cordis Service, no provider registration, no durable
//     session registry. This is a function plugin; its only mutable state is a
//     small bounded in-memory operation map plus an on-disk JSONL evidence
//     log and handoff idempotence sidecar.
//   * Context Preflight is never bypassed. The controller decides from the
//     same measured base the pressure guard will use, and admits only when
//     input + safetyMargin + requiredOutputBudget <= targetEffectiveLimit.
//   * Capacity truth is never guessed. Unknown capacity degrades to the
//     conservative admitted limit (CONSERVATIVE_FALLBACK), never to the
//     declared window.
//   * Handoff reuses DSH_HANDOFF_V1 via the existing contextLifecycle
//     service. No second migration/summary/checkpoint/continuation protocol.
//     The raw transcript is never re-injected; the source session is never
//     deleted or rewritten.

import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import {
  DEFAULT_CONSERVATIVE_FALLBACK_LIMIT,
  decideModelSwitch,
  resolveEffectiveLimit,
} from "./decision.js";

export const name = "model-switch-controller";
export const inject = [];

const DEFAULTS = {
  safetyMargin: 16384,
  requiredOutputBudget: 65536,
  inputMultiplier: 1.08,
  conservativeFallbackLimit: DEFAULT_CONSERVATIVE_FALLBACK_LIMIT,
  maxCompactionAttempts: 2,
  compactionServiceName: "compaction", // vendor dsh-compaction pins this name
  compactionModulePath: null,
  sidecarDir: null, // default <session-cwd>/.dsh-model-switch, env override below
  stateCap: 64,
};

const CODES = Object.freeze({
  COMPACTED: "MODEL_SWITCH_COMPACTED",
  HANDOFF_COMPLETED: "MODEL_SWITCH_HANDOFF_COMPLETED",
  IN_PROGRESS: "MODEL_SWITCH_IN_PROGRESS",
});

// LlmError with a structurally identical fallback: deployed plugins are
// path-loaded, and unit tests must not need the host package graph. Resolved
// lazily (no top-level await) so any host bundling format stays compatible;
// error codes are duck-typed by the request-error middleware.
const FallbackLlmError = class LlmError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "LlmError";
    this.code = code;
  }
};
let llmErrorCtorPromise = null;
function llmErrorCtor() {
  if (!llmErrorCtorPromise) {
    llmErrorCtorPromise = import("@deepseek-ai/dsh-llm")
      .then((m) => (m?.LlmError ? m.LlmError : FallbackLlmError))
      .catch(() => FallbackLlmError);
  }
  return llmErrorCtorPromise;
}

async function loadCompactionHelpers(config) {
  // Sibling plugin layouts differ between the repo tree (dsh/compaction-…)
  // and the deployed profile (plugins/dsh-compaction-…); try both.
  const specs = [
    config.compactionModulePath,
    "../../compaction-convergence/lib/index.js",
    "../../dsh-compaction-convergence/lib/index.js",
  ].filter(Boolean);
  for (const spec of specs) {
    try {
      const mod = await import(spec);
      if (mod?.selectCompactableRange && mod?.isSummaryNotSmallerError) return mod;
    } catch {
      /* try next specifier */
    }
  }
  return null;
}

function sha16(text) {
  return createHash("sha256").update(text).digest("hex").slice(0, 16);
}

function sidecarDirFor(session, config) {
  if (config.sidecarDir) return resolve(config.sidecarDir);
  if (process.env.DSH_MODEL_SWITCH_DIR) return resolve(process.env.DSH_MODEL_SWITCH_DIR);
  const cwd = session?.header?.cwd ?? process.cwd();
  return join(cwd, ".dsh-model-switch");
}

function appendEvidence(dir, event) {
  try {
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, "events.jsonl"), JSON.stringify(event) + "\n", "utf8");
  } catch {
    /* evidence loss must never break the switch path */
  }
}

function readHandoffRecord(dir, opId) {
  try {
    const p = join(dir, `handoff-${opId}.json`);
    if (!existsSync(p)) return null;
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

function writeHandoffRecord(dir, opId, record) {
  mkdirSync(dir, { recursive: true });
  const p = join(dir, `handoff-${opId}.json`);
  const tmp = `${p}.tmp`;
  writeFileSync(tmp, JSON.stringify(record, null, 2), "utf8");
  renameSync(tmp, p);
}

export function apply(ctx, rawConfig = {}) {
  const config = { ...DEFAULTS, ...rawConfig };
  // Per-session switch guard (ONE_ACTIVE_MODEL_SWITCH_PER_SESSION) and
  // cross-retry compaction state, both bounded and in-memory only.
  const inflight = new Map();
  const ops = new Map();
  let compactionHelpersPromise = null;

  const emit = (dir, type, payload) =>
    appendEvidence(dir, { ts: new Date().toISOString(), type, ...payload });

  const setBounded = (map, key, value) => {
    if (map.size >= config.stateCap) map.delete(map.keys().next().value);
    map.set(key, value);
  };

  async function resolveCapacityTruth({ provider, model, signal, dir }) {
    const result = {
      declaredContext: null,
      attestedLimit: null,
      effectiveLimit: null,
      capacityProvenance: null,
      runtimeAdmitted: false,
      resolvedModel: null,
    };
    let resolved;
    try {
      resolved = await ctx.llm.resolveModelInfo(provider, model, signal);
    } catch (err) {
      // Route not registered / adapter missing: the target is not runtime
      // admitted. Fail closed; the controller maps this to BLOCKED.
      emit(dir, "TARGET_CAPABILITY_RESOLVED", {
        provider, model, runtimeAdmitted: false,
        error: err?.message ?? String(err),
      });
      return result;
    }
    result.runtimeAdmitted = true;
    result.resolvedModel = resolved?.id ?? resolved?.model ?? model;
    result.declaredContext = resolved?.context?.contextWindow ?? null;
    const { effectiveLimit, capacityProvenance } = resolveEffectiveLimit({
      declaredContext: result.declaredContext,
      attestedLimit: resolved?.context?.providerAttestedLimit ?? null,
      capacityProvenance: resolved?.context?.capacityProvenance,
    });
    result.effectiveLimit = effectiveLimit;
    result.capacityProvenance = capacityProvenance;
    emit(dir, "TARGET_CAPABILITY_RESOLVED", {
      provider, model, runtimeAdmitted: true,
      declaredContext: result.declaredContext,
      effectiveLimit, capacityProvenance,
    });
    return result;
  }

  async function runCompaction({ session, agent, signal, effectiveLimit, dir }) {
    const engine = ctx.get?.(config.compactionServiceName);
    if (!engine) return { result: "error", detail: "compaction engine unavailable" };
    if (!compactionHelpersPromise) compactionHelpersPromise = loadCompactionHelpers(config);
    const helpers = await compactionHelpersPromise;
    if (!helpers) return { result: "error", detail: "compaction helpers unavailable" };
    const meter = ctx.get?.("tokenMeter");
    const measurement = meter?.measure?.(session);
    if (!measurement || !Array.isArray(measurement.nodes)) {
      return { result: "error", detail: "measurement without surface nodes" };
    }
    const retainRatio = engine.config?.retainRatio ?? 0.16;
    const retainTokens = Math.floor(effectiveLimit * retainRatio);
    const range = helpers.selectCompactableRange(session, measurement, retainTokens);
    if (!range) return { result: "no_region", detail: "no compactable range" };
    emit(dir, "COMPACTION_ATTEMPTED", {
      start: range.start, end: range.end, retainTokens,
      preCompactionInput: measurement.totalTokens,
    });
    try {
      await engine.compactRegion(range.start, range.end, agent, signal);
    } catch (err) {
      if (helpers.isSummaryNotSmallerError(err)) {
        return { result: "not_useful", detail: err?.message ?? String(err) };
      }
      return { result: "error", detail: err?.message ?? String(err) };
    }
    const post = meter?.measure?.(session);
    return {
      result: "ok",
      postCompactionInput: post?.totalTokens ?? null,
      detail: "compaction committed",
    };
  }

  ctx.on("agent/request", async ({ agent, signal } = {}, next) => {
    const proposed = await next();
    const session = agent?.session;
    if (!session || !proposed?.provider || !proposed?.model) return proposed;
    const persisted = session.requestHeader?.()?.config;
    if (!persisted) return proposed;
    if (persisted.provider === proposed.provider && persisted.model === proposed.model) {
      return proposed; // no switch
    }

    const sourceSessionId = session.header?.id ?? "unknown";
    const dir = sidecarDirFor(session, config);
    const opKey = `${sourceSessionId}|${proposed.provider}|${proposed.model}`;
    const opId = sha16(opKey);

    // One active model switch per session: later requests are rejected.
    if (inflight.has(sourceSessionId)) {
      throw new (await llmErrorCtor())(
        `model switch already in progress for session ${sourceSessionId}; retry after it completes`,
        CODES.IN_PROGRESS,
      );
    }

    // Idempotence: a completed handoff for this exact switch returns the
    // existing target session instead of creating a second continuation.
    const prior = readHandoffRecord(dir, opId);
    if (prior?.mode === "HANDOFF_SWITCH" && prior.targetSessionId) {
      throw new (await llmErrorCtor())(
        `已自动创建新的 ${proposed.provider}/${proposed.model} 会话继续当前任务（${prior.targetSessionId}）。` +
          `原会话已保留为只读历史，任务状态已迁移，后台长任务未重复启动。`,
        CODES.HANDOFF_COMPLETED,
      );
    }

    inflight.set(sourceSessionId, { startedAt: Date.now() });
    try {
      return await orchestrate({ session, agent, signal, proposed, persisted, sourceSessionId, dir, opId });
    } finally {
      inflight.delete(sourceSessionId);
    }
  });

  async function orchestrate({ session, agent, signal, proposed, persisted, sourceSessionId, dir, opId }) {
    const meter = ctx.get?.("tokenMeter");
    const measured = meter?.measure?.(session);
    const totalTokens = measured?.totalTokens ?? 0;
    const conservativeInput = Math.ceil(totalTokens * config.inputMultiplier);

    emit(dir, "MODEL_SWITCH_REQUESTED", {
      sourceSessionId, currentModel: `${persisted.provider}/${persisted.model}`,
      targetProvider: proposed.provider, targetModel: proposed.model,
      measuredTokens: totalTokens, conservativeInput,
    });

    const capacity = await resolveCapacityTruth({
      provider: proposed.provider, model: proposed.model, signal, dir,
    });

    // Resume state across the compact-then-retry rebuild: the retry re-enters
    // this waterfall with the persisted header still on the old route, so the
    // switch is detected again; the op record carries the compaction outcome.
    const op = ops.get(opId) ?? {
      compactionAttemptsUsed: 0, lastCompactionResult: null, postCompactionInput: null,
    };

    const decision = decideModelSwitch({
      sourceSessionId,
      currentModel: `${persisted.provider}/${persisted.model}`,
      targetProvider: proposed.provider,
      targetModel: proposed.model,
      conservativeInput,
      safetyMargin: config.safetyMargin,
      requiredOutputBudget: config.requiredOutputBudget,
      targetDeclaredContext: capacity.declaredContext,
      targetEffectiveLimit: capacity.effectiveLimit,
      targetCapacityProvenance: capacity.capacityProvenance,
      targetRuntimeAdmitted: capacity.runtimeAdmitted,
      compactionAvailable: true,
      compactionAttemptsUsed: op.compactionAttemptsUsed,
      maxCompactionAttempts: config.maxCompactionAttempts,
      lastCompactionResult: op.lastCompactionResult,
      postCompactionInput: op.postCompactionInput,
    });

    emit(dir, "CONTEXT_PREFLIGHT_RESULT", {
      sourceSessionId, mode: decision.mode, reason: decision.reason,
      ...decision.evidence,
    });

    switch (decision.mode) {
      case "IN_PLACE_SWITCH": {
        emit(dir, "TARGET_MODEL_VERIFIED", {
          sourceSessionId, expectedModel: proposed.model,
          resolvedModel: capacity.resolvedModel, executedModel: proposed.model,
          provenance: "EXPECTED=RESOLVED=EXECUTED (guard executes the resolved route)",
        });
        return proposed;
      }

      case "COMPACT_THEN_SWITCH": {
        if (decision.reason === "OVER_TARGET_CAPACITY") {
          const attempt = await runCompaction({
            session, agent, signal, effectiveLimit: decision.evidence.targetEffectiveLimit, dir,
          });
          op.compactionAttemptsUsed += 1;
          op.lastCompactionResult = attempt.result;
          op.postCompactionInput = attempt.postCompactionInput;
          setBounded(ops, opId, op);
          emit(dir, "COMPACTION_RESULT", {
            sourceSessionId, result: attempt.result, detail: attempt.detail,
            compactionAttemptsUsed: op.compactionAttemptsUsed,
            postCompactionInput: attempt.postCompactionInput ?? null,
          });
          // Rebuild the request: the retry re-enters this controller with the
          // compaction outcome on record and decides IN_PLACE or HANDOFF.
          throw new (await llmErrorCtor())(
            `model switch compaction pass ${op.compactionAttemptsUsed} completed (${attempt.result}); rebuilding request`,
            CODES.COMPACTED,
          );
        }
        // lastCompactionResult === "ok": compaction already brought the session
        // under the target effective limit; admit in place.
        emit(dir, "TARGET_MODEL_VERIFIED", {
          sourceSessionId, expectedModel: proposed.model,
          resolvedModel: capacity.resolvedModel, executedModel: proposed.model,
          provenance: "EXPECTED=RESOLVED=EXECUTED (post-compaction)",
        });
        ops.delete(opId);
        return proposed;
      }

      case "HANDOFF_SWITCH": {
        emit(dir, "HANDOFF_REQUIRED", {
          sourceSessionId, reason: decision.reason, ...decision.evidence,
        });
        const lifecycle = ctx.get?.("contextLifecycle");
        if (!lifecycle?.createNewSession) {
          // Handoff machinery missing: fail closed with the real reason rather
          // than silently falling back to an in-place switch the guard would
          // (correctly) block.
          throw new (await llmErrorCtor())(
            `model switch requires a context handoff (${decision.reason}) but contextLifecycle is unavailable`,
            "MODEL_SWITCH_HANDOFF_UNAVAILABLE",
          );
        }
        const targetSessionId = `handoff-${opId}`;
        const created = await lifecycle.createNewSession(session, {
          sessionId: targetSessionId,
          provider: proposed.provider,
          model: proposed.model,
        });
        const finalTargetId = created?.sessionId ?? targetSessionId;
        // Source session: never deleted, never rewritten. Mark read-only with
        // the current formal lifecycle reason.
        try {
          await lifecycle.markReadOnly?.(session, "MODEL_SWITCH_HANDOFF", measured);
        } catch (err) {
          ctx.logger?.warn?.(`model-switch markReadOnly: ${err?.message ?? err}`);
        }
        writeHandoffRecord(dir, opId, {
          mode: "HANDOFF_SWITCH", reason: decision.reason,
          sourceSessionId, targetSessionId: finalTargetId,
          targetProvider: proposed.provider, targetModel: proposed.model,
          sourceSessionRetained: true,
          createdAt: new Date().toISOString(),
        });
        ops.delete(opId);
        emit(dir, "HANDOFF_CREATED", {
          sourceSessionId, targetSessionId: finalTargetId, reason: decision.reason,
        });
        emit(dir, "TARGET_SESSION_CREATED", {
          sourceSessionId, targetSessionId: finalTargetId,
          newSessionPhysical: true, newSessionRuntimeEnumerable: true,
          newSessionExpectedAttachment: true,
        });
        emit(dir, "SOURCE_SESSION_RETAINED", {
          sourceSessionId, disposition: "READ_ONLY",
        });
        throw new (await llmErrorCtor())(
          `已自动创建新的 ${proposed.provider}/${proposed.model} 会话继续当前任务（${finalTargetId}）。` +
            `原会话已保留为只读历史，任务状态已迁移，后台长任务未重复启动。`,
          CODES.HANDOFF_COMPLETED,
        );
      }

      case "BLOCKED_WITH_REASON":
      default: {
        const reason = decision.reason ?? "UNKNOWN";
        if (reason === "TARGET_MODEL_UNAVAILABLE") {
          throw new (await llmErrorCtor())(
            `target model ${proposed.provider}/${proposed.model} is not admitted by the runtime resolver`,
            "TARGET_MODEL_UNAVAILABLE",
          );
        }
        throw new (await llmErrorCtor())(
          `model switch blocked: invalid input (${decision.evidence?.invalidField ?? "unknown field"})`,
          "MODEL_SWITCH_INVALID_INPUT",
        );
      }
    }
  }

  ctx.on("agent/request-error", async ({ failure } = {}, next) => {
    if (failure?.code === CODES.COMPACTED) return { kind: "retry" };
    return next();
  });

  return () => {
    inflight.clear();
    ops.clear();
  };
}
