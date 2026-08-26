import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import {
  apply,
  createJsonBridge,
  normalizeResponse,
  structuredError
} from "../src/index.mjs";

function fakeContext() {
  const registrations = new Map();
  return {
    registrations,
    ctx: {
      tools: {
        register(definition) {
          registrations.set(definition.name, definition);
          return () => registrations.delete(definition.name);
        }
      }
    }
  };
}

function spawnReply({ payload, code = 0, stderr = "", waitForKill = false } = {}) {
  return (_command, args, options) => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.invocation = { args, options };
    child.killed = false;
    child.kill = () => {
      child.killed = true;
      child.stdout.end();
      child.stderr.end();
      child.emit("close", 1, "SIGTERM");
    };
    if (!waitForKill) queueMicrotask(() => {
      if (payload !== undefined) child.stdout.end(`${JSON.stringify(payload)}\n`);
      else child.stdout.end();
      if (stderr) child.stderr.end(stderr);
      else child.stderr.end();
      child.emit("close", code, null);
    });
    return child;
  };
}

test("apply registers all research tools and disposes only registrations", async () => {
  const { ctx, registrations } = fakeContext();
  const dispose = apply(ctx, {
    python: "python",
    cliModule: "./fake-cli.py",
    workspace: "workspace"
  });

  assert.deepEqual([...registrations.keys()], [
    "research_init",
    "research_create",
    "research_inspect",
    "research_validate",
    "research_execute",
    "research_status",
    "research_evidence",
    "research_compare",
    "research_continue",
    "research_verify",
    "research_sync-init",
    "research_sync-push",
    "research_sync-pull"
  ]);
  assert.equal(registrations.get("research_create").name, "research_create");
  assert.equal(registrations.get("research_create").output.schema.type, "object");
  dispose();
  dispose();
  assert.equal(registrations.size, 0);
});

test("tool invocation bridges relative CLI args and normalizes JSON", async () => {
  let invocation;
  const { ctx, registrations } = fakeContext();
  apply(ctx, {
    workspace: "workspace",
    cliModule: "./fake-cli.py",
    spawn(command, args, options) {
      invocation = { command, args, options };
      return spawnReply({
        payload: { ok: true, code: "CREATED", message: "created", details: { id: "r1" } }
      })(command, args, options);
    }
  });

  const result = await registrations.get("research_create").execute({ spec: "spec.json" });
  assert.equal(result.ok, true);
  assert.equal(result.code, "CREATED");
  assert.equal(result.protocolVersion, "1.0");
  assert.deepEqual(invocation.args.slice(-5), ["--workspace", "workspace", "--spec", "spec.json", "--json"]);
  assert.equal(invocation.options.cwd, "workspace");
});

test("bridge forwards execution, revision, comparison, and parent controls", async () => {
  const invocations = [];
  const { ctx, registrations } = fakeContext();
  apply(ctx, {
    workspace: "workspace",
    cliModule: "./fake-cli.py",
    spawn(command, args, options) {
      invocations.push({ command, args, options });
      return spawnReply({ payload: { ok: true, details: {} } })(command, args, options);
    }
  });
  await registrations.get("research_execute").execute({
    researchId: "r1", runId: "run1", revisionId: "rev1", maxItems: 1
  });
  await registrations.get("research_compare").execute({ researchId: "r1", runId: "run1", metric: "cost" });
  await registrations.get("research_create").execute({ spec: "spec.json", parentRevisionId: "parent1" });
  assert.deepEqual(invocations[0].args.slice(-9), [
    "--research-id", "r1", "--run-id", "run1", "--revision-id", "rev1", "--max-items", "1", "--json"
  ]);
  assert.deepEqual(invocations[1].args.slice(-7), [
    "--research-id", "r1", "--run-id", "run1", "--metric", "cost", "--json"
  ]);
  assert.deepEqual(invocations[2].args.slice(-7), [
    "--workspace", "workspace", "--spec", "spec.json", "--parent-revision-id", "parent1", "--json"
  ]);
});

test("bridge exposes verify and sync lifecycle commands", async () => {
  const invocations = [];
  const { ctx, registrations } = fakeContext();
  apply(ctx, {
    workspace: "workspace",
    cliModule: "./fake-cli.py",
    spawn(command, args, options) {
      invocations.push(args);
      return spawnReply({ payload: { ok: true, details: {} } })(command, args, options);
    }
  });
  await registrations.get("research_init").execute({ workspaceId: "ws", deviceId: "device-a" });
  await registrations.get("research_verify").execute({});
  await registrations.get("research_sync-push").execute({ remote: "remote", deviceId: "device-a" });
  assert.deepEqual(invocations[0].slice(-7), [
    "--workspace", "workspace", "--workspace-id", "ws", "--device-id", "device-a", "--json"
  ]);
  assert.deepEqual(invocations[1].slice(-3), ["--workspace", "workspace", "--json"]);
  assert.deepEqual(invocations[2].slice(-7), [
    "--workspace", "workspace", "--device-id", "device-a", "--remote", "remote", "--json"
  ]);
});

test("bridge returns structured validation errors without spawning", async () => {
  let called = false;
  const invoke = createJsonBridge({ spawn: () => { called = true; } });
  const result = await invoke("inspect", {});
  assert.equal(called, false);
  assert.equal(result.ok, false);
  assert.equal(result.code, "WORKSPACE_REQUIRED");
  assert.equal(result.protocolVersion, "1.0");
});

test("bridge exposes CLI failures as structured errors", async () => {
  const invoke = createJsonBridge({
    workspace: "workspace",
    cliModule: "./fake-cli.py",
    spawn: spawnReply({ code: 2, stderr: "bad spec" })
  });
  const result = await invoke("validate", { spec: "bad.json" });
  assert.equal(result.ok, false);
  assert.equal(result.code, "CLI_FAILED");
  assert.match(result.message, /bad spec/);
});

test("tool cancellation terminates the child and returns a structured result", async () => {
  const controller = new AbortController();
  let child;
  const { ctx, registrations } = fakeContext();
  apply(ctx, {
    workspace: "workspace",
    cliModule: "./fake-cli.py",
    spawn(...args) {
      child = spawnReply({ waitForKill: true })(...args);
      return child;
    }
  });
  const pending = registrations.get("research_inspect").execute(
    { researchId: "r1" },
    { signal: controller.signal }
  );
  controller.abort();
  const result = await pending;
  assert.equal(child.killed, true);
  assert.equal(result.ok, false);
  assert.equal(result.code, "CANCELLED");
});

test("normalizers always include the stable envelope", () => {
  assert.deepEqual(normalizeResponse({ result: { value: 1 } }), {
    result: { value: 1 },
    ok: true,
    code: "OK",
    message: "Research CLI completed.",
    details: { result: { value: 1 } },
    protocolVersion: "1.0"
  });
  assert.equal(structuredError(new Error("nope")).protocolVersion, "1.0");
});
