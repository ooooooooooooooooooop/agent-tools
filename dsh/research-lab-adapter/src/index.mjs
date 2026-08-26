import { spawn as nodeSpawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

export const PROTOCOL_VERSION = "1.0";
export const DEFAULT_CLI_MODULE = "research_lab";

const TOOL_NAMES = [
  "init",
  "create",
  "inspect",
  "validate",
  "execute",
  "status",
  "evidence",
  "compare",
  "continue",
  "verify",
  "sync-init",
  "sync-push",
  "sync-pull"
];

const TOOL_DESCRIPTIONS = {
  init: "Initialize a new Research Workspace.",
  create: "Create a research revision through the Research CLI.",
  inspect: "Inspect a research revision through the Research CLI.",
  validate: "Validate a research specification through the Research CLI.",
  execute: "Execute or resume a research run through the Research CLI.",
  status: "Read research status through the Research CLI.",
  evidence: "Read research evidence through the Research CLI.",
  compare: "Compare research outputs through the Research CLI.",
  continue: "Continue a research revision through the Research CLI.",
  verify: "Verify Research Workspace metadata and CAS integrity.",
  "sync-init": "Initialize a Git metadata and filesystem CAS remote.",
  "sync-push": "Push CAS-first research metadata from this device.",
  "sync-pull": "Pull and union research metadata after CAS verification."
};

const INPUT_PROPERTIES = {
  workspace: { type: "string", description: "Research workspace path." },
  spec: { type: "string", description: "Research specification file." },
  researchId: { type: "string", description: "Research identifier." },
  runId: { type: "string", description: "Run identifier." },
  evidenceId: { type: "string", description: "Evidence identifier." },
  workspaceId: { type: "string", description: "Workspace identifier." },
  deviceId: { type: "string", description: "Device identifier." },
  parentRevisionId: { type: "string", description: "Explicit parent revision identifier." },
  revisionId: { type: "string", description: "Explicit research revision identifier." },
  maxItems: { type: "integer", description: "Maximum new model/case items for this execution." },
  metric: { type: "string", description: "Comparison metric override." },
  remote: { type: "string", description: "Portable sync remote path or mounted location." }
};

export class BridgeError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
    this.details = details;
  }
}

export function structuredError(error, fallbackCode = "BRIDGE_ERROR") {
  const code = error?.code || fallbackCode;
  const message = error?.message || String(error);
  const details = error?.details && typeof error.details === "object"
    ? error.details
    : {};
  return {
    ok: false,
    code,
    message,
    details,
    protocolVersion: PROTOCOL_VERSION
  };
}

export function normalizeResponse(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {
      ok: true,
      code: "OK",
      message: "Research CLI completed.",
      details: value ?? null,
      protocolVersion: PROTOCOL_VERSION
    };
  }

  const ok = value.ok !== false;
  return {
    ...value,
    ok,
    code: value.code || (ok ? "OK" : "BRIDGE_ERROR"),
    message: value.message || (ok ? "Research CLI completed." : "Research CLI failed."),
    details: value.details && typeof value.details === "object" && !Array.isArray(value.details)
      ? value.details
      : (Object.hasOwn(value, "result") ? { result: value.result } : {}),
    protocolVersion: value.protocolVersion || PROTOCOL_VERSION
  };
}

function readString(value, name) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") {
    throw new BridgeError("INVALID_INPUT", `${name} must be a string.`);
  }
  return value;
}

function firstValue(input, ...names) {
  for (const name of names) {
    if (input?.[name] !== undefined) return input[name];
  }
  return undefined;
}

function addFlag(args, name, value) {
  if (value !== undefined && value !== null && value !== "") {
    args.push(name, String(value));
  }
}

function commandFlags(input, command, config) {
  const data = input && typeof input === "object" ? input : {};
  const args = [command];
  const workspace = readString(firstValue(data, "workspace", "workspacePath") ?? config.workspace, "workspace");
  const spec = readString(firstValue(data, "spec", "specFile"), "spec");
  const researchId = readString(firstValue(data, "researchId", "research_id"), "researchId");
  const runId = readString(firstValue(data, "runId", "run_id"), "runId");
  const evidenceId = readString(firstValue(data, "evidenceId", "evidence_id"), "evidenceId");
  const remote = readString(data.remote, "remote");

  if (command !== "validate" && !workspace) {
    throw new BridgeError("WORKSPACE_REQUIRED", `${command} requires workspace.`);
  }
  if (["inspect", "execute", "compare", "continue"].includes(command) && !researchId) {
    throw new BridgeError("RESEARCH_ID_REQUIRED", `${command} requires researchId.`);
  }
  if (["status", "evidence", "continue"].includes(command) && !runId) {
    throw new BridgeError("RUN_ID_REQUIRED", `${command} requires runId.`);
  }
  if (command.startsWith("sync-") && !remote) {
    throw new BridgeError("REMOTE_REQUIRED", `${command} requires remote.`);
  }
  if (["create", "validate"].includes(command) && !spec) {
    throw new BridgeError("SPEC_REQUIRED", `${command} requires spec.`);
  }

  addFlag(args, "--workspace", workspace);
  addFlag(args, "--spec", spec);
  addFlag(args, "--research-id", researchId);
  addFlag(args, "--run-id", runId);
  addFlag(args, "--evidence-id", evidenceId);
  addFlag(args, "--workspace-id", readString(firstValue(data, "workspaceId", "workspace_id"), "workspaceId"));
  addFlag(args, "--device-id", readString(firstValue(data, "deviceId", "device_id"), "deviceId"));
  addFlag(args, "--parent-revision-id", readString(firstValue(data, "parentRevisionId", "parent_revision_id"), "parentRevisionId"));
  addFlag(args, "--revision-id", readString(firstValue(data, "revisionId", "revision_id"), "revisionId"));
  addFlag(args, "--max-items", firstValue(data, "maxItems", "max_items"));
  addFlag(args, "--metric", readString(data.metric, "metric"));
  addFlag(args, "--remote", remote);
  args.push("--json");
  return args;
}

function isModuleName(value) {
  return typeof value === "string" && !value.startsWith(".") && !value.includes("/") && !value.includes("\\");
}

function resolveCliArguments(config = {}) {
  const cliModule = config.cliModule ?? config.cli ?? DEFAULT_CLI_MODULE;
  if (Array.isArray(cliModule)) return cliModule.map(String);
  if (typeof cliModule !== "string" || !cliModule) {
    throw new BridgeError("CLI_CONFIG_INVALID", "cliModule must be a non-empty string or argument array.");
  }
  if (config.module || isModuleName(cliModule)) return ["-m", config.module || cliModule];
  if (cliModule.startsWith("file:") || cliModule.startsWith(".") || cliModule.includes("/") || cliModule.includes("\\")) {
    const url = cliModule.startsWith("file:")
      ? new URL(cliModule)
      : new URL(cliModule, import.meta.url);
    return [fileURLToPath(url)];
  }
  return [cliModule];
}

function parseJsonOutput(stdout) {
  const text = String(stdout || "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      try {
        return JSON.parse(lines[index]);
      } catch {
        // Keep looking for the JSON record promised by the CLI bridge.
      }
    }
  }
  throw new BridgeError("INVALID_CLI_JSON", "Research CLI did not emit valid JSON.", { stdout: text });
}

function resolveSpawn(config) {
  if (typeof config.spawn === "function") return config.spawn;
  if (typeof config.spawnProcess === "function") return config.spawnProcess;
  return nodeSpawn;
}

export function createJsonBridge(config = {}) {
  const settings = { ...config };
  return async function invoke(command, input = {}, signal) {
    try {
      if (!TOOL_NAMES.includes(command)) {
        throw new BridgeError("UNKNOWN_TOOL", `Unknown research tool: ${command}.`);
      }
      const args = commandFlags(input, command, settings);
      const executable = settings.python ?? process.env.RESEARCH_PYTHON ?? "python";
      const cliArgs = [...resolveCliArguments(settings), ...args];
      const cwd = settings.cwd ?? settings.workspace ?? process.cwd();
      const env = { ...process.env, ...(settings.env || {}) };
      const child = resolveSpawn(settings)(executable, cliArgs, {
        cwd,
        env,
        stdio: ["ignore", "pipe", "pipe"]
      });

      const stdout = [];
      const stderr = [];
      child.stdout?.on("data", (chunk) => stdout.push(Buffer.from(chunk)));
      child.stderr?.on("data", (chunk) => stderr.push(Buffer.from(chunk)));

      const result = await new Promise((resolve, reject) => {
        let settled = false;
        const onAbort = () => {
          finish(reject, new BridgeError("CANCELLED", "Research CLI invocation was cancelled."));
          child.kill?.();
        };
        const finish = (callback, value) => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener?.("abort", onAbort);
          callback(value);
        };
        if (signal?.aborted) return onAbort();
        signal?.addEventListener?.("abort", onAbort, { once: true });
        child.once?.("error", (error) => finish(reject, new BridgeError("CLI_SPAWN_ERROR", error.message, { cause: error.code })));
        child.once?.("close", (code, exitSignal) => finish(resolve, { code, signal: exitSignal }));
        child.on?.("exit", (code, exitSignal) => {
          if (child.listenerCount?.("close") === 0) finish(resolve, { code, signal: exitSignal });
        });
      });

      const outText = Buffer.concat(stdout).toString("utf8");
      const errText = Buffer.concat(stderr).toString("utf8").trim();
      let parsed = null;
      try {
        parsed = parseJsonOutput(outText);
      } catch (error) {
        if (result.code !== 0) {
          throw new BridgeError("CLI_FAILED", errText || error.message, {
            exitCode: result.code,
            signal: result.signal,
            stderr: errText,
            stdout: outText.trim()
          });
        }
        throw error;
      }
      if (result.code !== 0) {
        if (parsed && typeof parsed === "object") {
          return normalizeResponse({
            ...parsed,
            ok: false,
            details: parsed.details ?? { stderr: errText, exitCode: result.code, signal: result.signal }
          });
        }
        throw new BridgeError("CLI_FAILED", errText || "Research CLI failed.", {
          exitCode: result.code,
          signal: result.signal,
          stderr: errText
        });
      }
      return normalizeResponse(parsed);
    } catch (error) {
      return structuredError(error);
    }
  };
}

const INPUT_SCHEMA = {
  type: "object",
  properties: INPUT_PROPERTIES,
  additionalProperties: true
};

const OUTPUT_SCHEMA = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    code: { type: "string" },
    message: { type: "string" },
    details: {
      oneOf: [
        { type: "object", additionalProperties: true },
        { type: "array" },
        { type: "string" },
        { type: "number" },
        { type: "boolean" },
        { type: "null" }
      ]
    },
    protocolVersion: { type: "string" }
  },
  required: ["ok", "code", "message", "details", "protocolVersion"],
  additionalProperties: true
};

export function buildToolDefinitions(invoke) {
  return Object.fromEntries(TOOL_NAMES.map((name) => [name, {
    name: `research_${name}`,
    description: TOOL_DESCRIPTIONS[name],
    parameters: INPUT_SCHEMA,
    output: {
      schema: OUTPUT_SCHEMA,
      render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }]
    },
    execute: (input = {}, exec) => invoke(name, input, exec?.signal)
  }]));
}

function disposeRegistration(registration) {
  if (typeof registration === "function") return registration;
  if (registration && typeof registration.dispose === "function") return () => registration.dispose();
  if (registration && typeof registration.close === "function") return () => registration.close();
  return () => {};
}

export function apply(ctx, config = {}) {
  const register = ctx?.tools?.register;
  if (typeof register !== "function") {
    throw new BridgeError("TOOLS_REGISTER_UNAVAILABLE", "ctx.tools.register is required.");
  }

  const invoke = createJsonBridge(config);
  const definitions = buildToolDefinitions(invoke);
  const disposers = [];
  for (const name of TOOL_NAMES) {
    const registration = register.call(ctx.tools, definitions[name]);
    disposers.push(disposeRegistration(registration));
  }

  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    for (const dispose of disposers.splice(0).reverse()) dispose();
  };
}

export default apply;

// Keep these imports available to bundlers that expose file URLs without path helpers.
export { path, pathToFileURL };
