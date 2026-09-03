// adapter.test.mjs — integration tests with a mocked cordis ctx (spec §38).
// Exercises the real plugin wiring: agent/request detection, the three-path
// controller, compact-then-retry rebuild, DSH_HANDOFF_V1 reuse, idempotence,
// concurrency bound, and the retry middleware. No host package graph needed.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const { apply } = await import("../lib/index.js");

// --- fixtures ---------------------------------------------------------------

function makeCtx({ totalTokens = 90_000, resolved, services = {} } = {}) {
  const handlers = {};
  const calls = { provide: 0, createNewSession: 0, markReadOnly: 0, compactRegion: 0 };
  const measurement = { totalTokens, nodes: [{ id: "n1" }] };
  const meter = {
    measure: () => measurement,
  };
  const engine = services.engine ?? {
    config: { retainRatio: 0.16 },
    compactRegion: async () => {
      calls.compactRegion += 1;
      services.compactBehavior?.(measurement);
    },
  };
  const lifecycle = services.lifecycle ?? {
    createNewSession: async (_session, options) => {
      calls.createNewSession += 1;
      return { sessionId: options.sessionId };
    },
    markReadOnly: async () => {
      calls.markReadOnly += 1;
    },
  };
  const ctx = {
    handlers,
    calls,
    on: (event, fn) => {
      handlers[event] = fn;
      return () => {};
    },
    get: (name) => ({
      tokenMeter: meter,
      compaction: engine,
      contextLifecycle: lifecycle,
    })[name],
    llm: {
      resolveModelInfo: async (provider, model) => {
        if (resolved instanceof Error) throw resolved;
        return resolved ?? { id: model, context: { contextWindow: 1_048_576 } };
      },
    },
    logger: { warn: () => {} },
    provide: () => {
      calls.provide += 1;
    },
  };
  return ctx;
}

function makeAgent({ id = "sess-1", persisted = { provider: "cpa", model: "gpt-5.6-luna-max" }, cwd } = {}) {
  return {
    session: {
      header: { id, cwd: cwd ?? process.cwd() },
      requestHeader: () => ({ config: persisted }),
    },
  };
}

const GEMINI = { provider: "cpa", model: "gemini-3.7-flash-high" };
const signal = { aborted: false };

function tmpSidecar() {
  return mkdtempSync(join(tmpdir(), "msw-test-"));
}

// Compaction helpers fixture: selectCompactableRange always returns a range;
// NOT_SMALLER-coded errors classify as not-useful.
function writeHelpersFixture(dir) {
  const p = join(dir, "helpers.mjs");
  writeFileSync(p, `
export function selectCompactableRange() { return { start: 0, end: 1 }; }
export function isSummaryNotSmallerError(err) { return err?.code === "NOT_SMALLER"; }
`, "utf8");
  return pathToFileURL(p).href;
}

function baseConfig(dir, over = {}) {
  return {
    sidecarDir: dir,
    compactionModulePath: writeHelpersFixture(dir),
    ...over,
  };
}

function events(dir) {
  const p = join(dir, "events.jsonl");
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter(Boolean).map(JSON.parse);
}

const nextFor = (proposed) => async () => proposed;

// --- tests ------------------------------------------------------------------

test("no switch: same route passes through untouched", async () => {
  const dir = tmpSidecar();
  const ctx = makeCtx({ resolved: { id: GEMINI.model, context: { contextWindow: 1_048_576 } } });
  apply(ctx, baseConfig(dir));
  const agent = makeAgent({ persisted: GEMINI });
  const out = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI));
  assert.deepEqual(out, GEMINI);
  assert.equal(events(dir).length, 0);
});

test("IN_PLACE: fits under effective limit; proposed config returned; evidence emitted", async () => {
  const dir = tmpSidecar();
  const ctx = makeCtx({ totalTokens: 90_000 }); // 97200 + 81920 <= 262144
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const out = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI));
  assert.deepEqual(out, GEMINI);
  const ev = events(dir).map((e) => e.type);
  for (const t of ["MODEL_SWITCH_REQUESTED", "TARGET_CAPABILITY_RESOLVED", "CONTEXT_PREFLIGHT_RESULT", "TARGET_MODEL_VERIFIED"]) {
    assert.ok(ev.includes(t), `missing ${t}`);
  }
  const preflight = events(dir).find((e) => e.type === "CONTEXT_PREFLIGHT_RESULT");
  assert.equal(preflight.mode, "IN_PLACE_SWITCH");
  assert.equal(preflight.targetCapacityProvenance, "CONSERVATIVE_FALLBACK");
  assert.equal(preflight.targetEffectiveLimit, 262144);
});

test("COMPACT_THEN_SWITCH: over limit -> compact -> retry rebuild -> admitted in place", async () => {
  const dir = tmpSidecar();
  // 392648 * 1.08 = 424060 conservative (the incident arithmetic), over 262144.
  const ctx = makeCtx({
    totalTokens: 392_648,
    services: {
      compactBehavior: (m) => {
        m.totalTokens = 150_000; // compaction reclaims the head
      },
    },
  });
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const first = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI))
    .then(() => "resolved", (err) => err);
  assert.equal(first.code, "MODEL_SWITCH_COMPACTED");
  assert.equal(ctx.calls.compactRegion, 1);
  // Retry pass: controller sees lastCompactionResult "ok" and admits.
  const second = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI));
  assert.deepEqual(second, GEMINI);
  // request-error middleware converts the compaction signal into a retry.
  const retry = await ctx.handlers["agent/request-error"](
    { failure: { code: "MODEL_SWITCH_COMPACTED" } },
    async () => "next",
  );
  assert.deepEqual(retry, { kind: "retry" });
  const passthrough = await ctx.handlers["agent/request-error"](
    { failure: { code: "SOMETHING_ELSE" } },
    async () => "next",
  );
  assert.equal(passthrough, "next");
  const ev = events(dir).map((e) => e.type);
  assert.ok(ev.includes("COMPACTION_ATTEMPTED"));
  assert.ok(ev.includes("COMPACTION_RESULT"));
});

test("compaction bounded: attempts never exceed maxCompactionAttempts", async () => {
  const dir = tmpSidecar();
  const ctx = makeCtx({
    totalTokens: 392_648,
    services: {
      compactBehavior: (m) => {
        m.totalTokens = 300_000; // reclaim less than needed: stays over
      },
    },
  });
  apply(ctx, baseConfig(dir, { maxCompactionAttempts: 2 }));
  const agent = makeAgent();
  await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  const third = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  // Third pass: attempts exhausted -> HANDOFF (or handoff-throw), never a 3rd compaction.
  assert.ok(third.code === "MODEL_SWITCH_HANDOFF_COMPLETED" || third.code === "MODEL_SWITCH_COMPACTED");
  assert.ok(ctx.calls.compactRegion <= 2, `compaction ran ${ctx.calls.compactRegion} times`);
});

test("HANDOFF: NOT_USEFUL -> DSH_HANDOFF_V1 via contextLifecycle; source retained; idempotent", async () => {
  const dir = tmpSidecar();
  const err = Object.assign(new Error("summary is not smaller"), { code: "NOT_SMALLER" });
  const ctx = makeCtx({
    totalTokens: 392_648,
    services: {
      compactBehavior: () => {
        throw err;
      },
    },
  });
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const first = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  assert.equal(first.code, "MODEL_SWITCH_COMPACTED");
  // Retry: NOT_USEFUL -> handoff.
  const second = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  assert.equal(second.code, "MODEL_SWITCH_HANDOFF_COMPLETED");
  assert.match(second.message, /已自动创建新的 cpa\/gemini-3\.7-flash-high 会话/);
  assert.match(second.message, /后台长任务未重复启动/);
  assert.equal(ctx.calls.createNewSession, 1);
  assert.equal(ctx.calls.markReadOnly, 1);
  // Deterministic target session id + idempotence: same switch never creates a
  // second continuation session; retry returns the existing target id.
  const third = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  assert.equal(third.code, "MODEL_SWITCH_HANDOFF_COMPLETED");
  assert.equal(ctx.calls.createNewSession, 1);
  const created = events(dir).find((e) => e.type === "TARGET_SESSION_CREATED");
  assert.match(created.targetSessionId, /^handoff-[0-9a-f]{16}$/);
  assert.equal(events(dir).filter((e) => e.type === "SOURCE_SESSION_RETAINED").length >= 1, true);
});

test("BLOCKED: target not runtime admitted -> TARGET_MODEL_UNAVAILABLE, no silent fallback", async () => {
  const dir = tmpSidecar();
  const ctx = makeCtx({ resolved: Object.assign(new Error("NO_ADAPTER"), { code: "NO_ADAPTER" }) });
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const err = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  assert.equal(err.code, "TARGET_MODEL_UNAVAILABLE");
  assert.equal(ctx.calls.createNewSession, 0);
  assert.equal(ctx.calls.compactRegion, 0);
});

test("concurrency: ONE_ACTIVE_MODEL_SWITCH_PER_SESSION; later request rejected", async () => {
  const dir = tmpSidecar();
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const ctx = makeCtx({ resolved: undefined });
  ctx.llm.resolveModelInfo = async () => {
    await gate;
    return { id: GEMINI.model, context: { contextWindow: 1_048_576 } };
  };
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const p1 = ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI));
  await new Promise((r) => setImmediate(r));
  const err = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI)).catch((e) => e);
  assert.equal(err.code, "MODEL_SWITCH_IN_PROGRESS");
  release();
  await p1;
});

test("no host-scoped provider: apply() never registers a service", async () => {
  const dir = tmpSidecar();
  const ctx = makeCtx();
  apply(ctx, baseConfig(dir));
  assert.equal(ctx.calls.provide, 0);
});

test("capacity evidence honest: UNKNOWN declared window never used as truth", async () => {
  const dir = tmpSidecar();
  // Resolver attests the real window -> PROVIDER_ATTESTED path is honored.
  const ctx = makeCtx({
    totalTokens: 500_000, // 540000 conservative
    resolved: {
      id: GEMINI.model,
      context: { contextWindow: 1_048_576, providerAttestedLimit: 1_048_576 },
    },
  });
  apply(ctx, baseConfig(dir));
  const agent = makeAgent();
  const out = await ctx.handlers["agent/request"]({ agent, signal }, nextFor(GEMINI));
  assert.deepEqual(out, GEMINI); // 540000 + 81920 <= 1048576 -> IN_PLACE
  const resolved = events(dir).find((e) => e.type === "TARGET_CAPABILITY_RESOLVED");
  assert.equal(resolved.capacityProvenance, "PROVIDER_ATTESTED");
});
