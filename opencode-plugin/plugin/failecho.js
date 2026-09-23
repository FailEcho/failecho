/**
 * FailEcho for OpenCode: advice in the failing tool's output, without the
 * model having to ask for it.
 *
 * OpenCode can be given FailEcho's MCP server, and then the model has to
 * decide to call a tool while it is busy failing at something else. Measured
 * over 32 runs in our lab, it decided to do that 0.19 times per run, and the
 * run that finally arrived under the strictest instruction we could write
 * used `bash` thirteen times and the tool zero. A tool a model must choose
 * loses to the task in front of it.
 *
 * A plugin does not ask. `tool.execute.after` fires for every tool OpenCode
 * runs -- `bash`, `webfetch`, and every MCP tool -- so a failure can be
 * classified, reported and answered where it happens, and the answer lands in
 * the output the model is already reading.
 *
 *     .opencode/plugin/failecho.js        (auto-discovered, nothing to configure)
 *
 * or from npm: `{ "plugin": ["failecho-opencode"] }` in `opencode.json`.
 *
 * What is sent, per call, and nothing else:
 *
 *     service     the API host, e.g. api.github.com, or the MCP server's name
 *     operation   the endpoint or tool, e.g. GET /repos, create_issue
 *     outcome     success | failure
 *     error_type  a class: rate_limit, server_error, timeout, auth_error, ...
 *     error_code  403, 429, 503, ...
 *     latency_ms
 *
 * Never the command, the arguments, the output, the URL beyond its host, a
 * file path, an environment variable or a token. The failure text is read
 * here, in this process, to pick the class -- and then dropped.
 *
 * FAILECHO_TEAM=<secret> is private mode: reports are stored for your team
 * alone, and your team's own evidence comes back in the same line.
 *
 * FAILECHO_DISABLED=1 turns everything off. FAILECHO_ADVISE=0 reports without
 * annotating. FAILECHO_ENDPOINT points somewhere else (a local instance, a
 * self-hosted one). FAILECHO_REPORTER_ID names this installation.
 */

const DEFAULT_ENDPOINT = "https://failecho.com";
const ADVICE_TIMEOUT_MS = 3000;
const REPORT_TIMEOUT_MS = 5000;
//: A failure and a later success on the same target inside this window is
//: read as a recovery: what the agent did after the failure worked.
const RECOVERY_WINDOW_MS = 120000;

const TRANSIENT = new Set(["rate_limit", "server_error", "timeout", "connection_error"]);

// Must match failecho_autoreport.ERROR_CLASSES and npm-relay/bin/proxy.js --
// a test holds the three together, or evidence reported from OpenCode and
// evidence reported from the wrapper would not join on one fingerprint.
const ERROR_CLASSES = [
  ["timeout", /time[d ]?\s?out|deadline exceeded|ETIMEDOUT/i],
  ["rate_limit", /\b429\b|rate.?limit|too many requests|quota/i],
  ["auth_error", /\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|authenticat|invalid (api )?key/i],
  ["not_found", /\b404\b|not found|no such/i],
  ["server_error", /\b5\d\d\b|internal (server )?error|service unavailable|bad gateway|upstream/i],
  ["validation_error", /\b4(00|22)\b|invalid|validation|required|must be|schema/i],
  ["connection_error", /ECONN(REFUSED|RESET)|ENOTFOUND|EPIPE|connection (refused|reset|closed|error)|network|socket/i],
];
const HTTP_CLASSES = new Set(["rate_limit", "auth_error", "not_found", "validation_error", "server_error"]);

//: Tools whose failures are the agent's own business: a missing file, a grep
//: that matched nothing, a patch that would not apply. Nobody else can act on
//: those, and reporting them is noise in a shared network.
const LOCAL_TOOLS = new Set([
  "read", "write", "edit", "patch", "multiedit", "glob", "grep", "list", "ls",
  "todowrite", "todoread", "task", "think",
]);

function truthy(value, dflt) {
  if (value === undefined || value === null || value === "") return dflt;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function classify(text) {
  const full = `ToolError: ${text}`;
  for (const [name, pattern] of ERROR_CLASSES) {
    if (pattern.test(full)) {
      let code = null;
      if (HTTP_CLASSES.has(name)) {
        const m = full.match(/\b([45]\d\d)\b/);
        if (m) code = m[1];
      }
      return [name, code];
    }
  }
  return ["error", null];
}

/** The first http(s) URL in a string, or null. */
function firstUrl(text) {
  const m = String(text || "").match(/https?:\/\/[^\s'"`;|&)>]+/);
  return m ? m[0] : null;
}

/**
 * What this call was against, named the way the network names things:
 * `service` is the API host or the MCP server, `operation` is the endpoint or
 * the tool. Returns null when the call is nobody else's business.
 *
 * The URL is used for its host and the first segment of its path. The rest --
 * query string, ids, the command around it -- is not looked at again.
 */
function target(tool, args) {
  const name = String(tool || "");
  if (LOCAL_TOOLS.has(name)) return null;

  if (name === "webfetch" || name === "fetch") {
    const url = firstUrl(args && (args.url || args.uri));
    return url ? fromUrl(url, "GET") : null;
  }
  if (name === "bash" || name === "shell") {
    const command = String((args && args.command) || "");
    const url = firstUrl(command);
    if (!url) return null;      // a local command: not shared infrastructure
    const method = (command.match(/-X\s+([A-Z]+)/) || [])[1] || "GET";
    return fromUrl(url, method);
  }
  // An MCP tool. OpenCode keys them `server_tool`; the server is the service,
  // exactly as the MCP proxy reports it.
  const cut = name.indexOf("_");
  if (cut > 0) return { service: name.slice(0, cut), operation: name.slice(cut + 1) };
  return { service: name, operation: name };
}

function fromUrl(url, method) {
  try {
    const parsed = new URL(url);
    const segment = parsed.pathname.split("/").filter(Boolean)[0];
    return {
      service: parsed.hostname.toLowerCase(),
      operation: `${method} /${segment || ""}`.trim(),
    };
  } catch {
    return null;
  }
}

/** The text of a tool result, for classification only. Never sent. */
function resultText(output) {
  if (!output || typeof output !== "object") return String(output || "");
  const parts = [];
  if (typeof output.output === "string") parts.push(output.output);
  if (typeof output.title === "string") parts.push(output.title);
  const meta = output.metadata;
  if (meta && typeof meta === "object") {
    for (const key of ["error", "stderr", "message", "description"]) {
      if (typeof meta[key] === "string") parts.push(meta[key]);
    }
  }
  return parts.join("\n").slice(0, 4000);
}

/** Whether this result is a failure. Exit status first, words second. */
function failed(output, text) {
  const meta = (output && output.metadata) || {};
  if (typeof meta.exit === "number") return meta.exit !== 0;
  if (meta.error === true || typeof meta.error === "string") return true;
  if (output && output.isError) return true;
  // No status to read (most MCP tools): the text has to say so, and it has to
  // say so in a way that also gives us a class. "error" alone does not.
  const [kind] = classify(text);
  return kind !== "error" && /error|fail|refus|denied|unable|cannot|could not/i.test(text);
}

export default async function failecho() {
  const endpoint = (process.env.FAILECHO_ENDPOINT || DEFAULT_ENDPOINT).replace(/\/+$/, "");
  const enabled = !truthy(process.env.FAILECHO_DISABLED, false);
  const advise = truthy(process.env.FAILECHO_ADVISE, true);
  const reporterId = process.env.FAILECHO_REPORTER_ID || null;
  const teamToken = process.env.FAILECHO_TEAM || null;

  const started = new Map();            // callID -> ms
  const lastFailure = new Map();        // "service|operation" -> {at, fingerprint, type}

  function headers() {
    const h = { "content-type": "application/json", "user-agent": "failecho-opencode" };
    if (reporterId) h["x-reporter-id"] = reporterId;
    if (teamToken) h["x-failecho-team"] = teamToken;
    return h;
  }

  async function post(path, body, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${endpoint}${path}`, {
        method: "POST", headers: headers(), body: JSON.stringify(body), signal: controller.signal,
      });
      return response.ok ? await response.json() : null;
    } catch {
      return null;                      // never the agent's problem
    } finally {
      clearTimeout(timer);
    }
  }

  /** One line of what the network knows, or null when it knows nothing. */
  function adviceLine(answer) {
    if (!answer || typeof answer !== "object") return null;
    const rec = answer.recommendation;
    const actions = (answer.recovery_actions || []).filter((a) => a && a.action);
    if (rec && rec.action) {
      const stats = actions.find((a) => a.action === rec.action);
      const counts = stats ? ` (worked ${stats.successes}/${stats.attempts})` : "";
      const who = rec.from_other_agents === false ? ", from your own history" : "";
      const warn = rec.warning ? ` Note: ${rec.warning}` : "";
      return `FailEcho: try ${rec.action}${counts}${who}.${warn}`;
    }
    // Private mode: the team's own recommendation when the public network
    // has none, labelled as the team's -- the same words as the wrapper's.
    const team = answer.team_evidence;
    if (team && typeof team === "object") {
      const teamActions = (team.recovery_actions || []).filter((a) => a && a.action);
      const teamRec = team.recommendation;
      if (teamRec && teamRec.action === "skip") {
        return "FailEcho (your team's own history): skip -- nothing your agents tried recently has fixed this failure.";
      }
      if (teamRec && teamRec.action) {
        const stats = teamActions.find((a) => a.action === teamRec.action);
        const counts = stats ? `, worked ${stats.successes}/${stats.attempts}` : "";
        return `FailEcho (your team's own history): try ${teamRec.action}${counts}.`;
      }
    }
    if (answer.status === "MAJOR" || answer.status === "DEGRADED") {
      const rate = answer.failure_rate && answer.failure_rate.last_5m;
      return `FailEcho: ${answer.service || "this service"} is ${answer.status.toLowerCase()} right now`
        + (rate ? ` (${Math.round(rate * 100)}% of calls failing in the last 5 minutes)` : "")
        + "; a retry now is unlikely to be the fix.";
    }
    if (actions.length) {
      const tried = actions.slice(0, 3).map((a) => `${a.action} worked ${a.successes}/${a.attempts}`).join(", ");
      return `FailEcho: no clear fix yet; other agents tried ${tried}.`;
    }
    if (team && typeof team === "object") {
      const seen = (team.recovery_actions || []).filter((a) => a && a.action && a.attempts);
      if (seen.length) {
        const tried = seen.slice(0, 3).map((a) => `${a.action} worked ${a.successes || 0}/${a.attempts}`).join(", ");
        return `FailEcho (your team's own history): no clear fix yet; your agents tried ${tried}.`;
      }
    }
    return null;
  }

  if (!enabled) return {};

  return {
    "tool.execute.before": async (input) => {
      started.set(input.callID, Date.now());
    },

    "tool.execute.after": async (input, output) => {
      const at = started.get(input.callID);
      started.delete(input.callID);
      const latency = at ? Date.now() - at : null;

      let where;
      try {
        where = target(input.tool, (input && input.args) || (output && output.args));
      } catch {
        return;
      }
      if (!where) return;                         // local, or nothing to name

      const key = `${where.service}|${where.operation}`;
      const text = resultText(output);

      if (!failed(output, text)) {
        const previous = lastFailure.get(key);
        if (previous && Date.now() - previous.at < RECOVERY_WINDOW_MS) {
          // It failed, the agent did something, it worked. That sequence is
          // the only evidence the network has about what fixes anything.
          lastFailure.delete(key);
          // The fingerprint comes back with the failure's own report, and the
          // success can arrive first. Waiting on the promise, not on a value
          // that may not be set yet, is what keeps the recovery from being
          // lost to timing -- a test caught exactly that race.
          previous.fingerprint.then((fingerprint) => {
            if (fingerprint) {
              post("/v1/outcome", { fingerprint, action: "retry", successful: true }, REPORT_TIMEOUT_MS);
            }
          });
        }
        post("/v1/observe", {
          service: where.service, operation: where.operation, outcome: "success",
          ...(latency === null ? {} : { latency_ms: latency }),
        }, REPORT_TIMEOUT_MS);
        return;
      }

      const [errorType, errorCode] = classify(text);
      const report = post("/v1/observe", {
        service: where.service, operation: where.operation, outcome: "failure",
        error_type: errorType, ...(errorCode ? { error_code: errorCode } : {}),
        ...(latency === null ? {} : { latency_ms: latency }),
      }, REPORT_TIMEOUT_MS);

      if (TRANSIENT.has(errorType)) {
        // Recorded now, with the fingerprint still on its way: see the
        // success path above.
        lastFailure.set(key, {
          at: Date.now(),
          fingerprint: report.then((answer) => (answer && answer.fingerprint) || null),
          type: errorType,
        });
      }

      if (!advise) return;
      const answer = await post("/v1/query", {
        service: where.service, operation: where.operation,
        error_type: errorType, ...(errorCode ? { error_code: errorCode } : {}),
      }, ADVICE_TIMEOUT_MS);
      const line = adviceLine(answer);
      if (!line) return;

      // Where the model is already looking. Appended, never replacing: the
      // tool's own output is what the agent asked for, and a plugin that
      // rewrites it is a plugin nobody should install.
      if (typeof output.output === "string") output.output = `${output.output}\n\n${line}`;
      else output.output = line;
      if (output.metadata && typeof output.metadata === "object") output.metadata.failecho = line;
    },
  };
}
