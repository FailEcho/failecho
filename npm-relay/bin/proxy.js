/**
 * FailEcho in front of another MCP server: advice inside the failing tool's error.
 *
 * The Node twin of failecho_mcp/proxy.py, same behaviour, same error classes,
 * same advice line, no dependencies. Run it where the client would have
 * started the server:
 *
 *     failecho-mcp proxy -- npx -y @modelcontextprotocol/server-github
 *     failecho-mcp proxy --header "Authorization: Bearer $TOKEN" -- https://mcp.example.com/mcp
 *
 * Every message passes through as the same bytes, both ways, except the
 * response to a tools/call that failed: that one gets one line of the
 * network's advice (a text item on an isError result, a sentence on a
 * JSON-RPC error's message). Every call's outcome is reported as its shape
 * only -- the server's name, the tool, an error class, a code, the latency.
 * Arguments, results and error text never leave; the text is read here to
 * pick the class. A transiently failed call repeated with the same arguments
 * inside two minutes is reported as a retry and whether it worked; the
 * arguments are compared as a hash in memory.
 *
 * FAILECHO_DISABLED=1 is a plain pipe. FAILECHO_ADVISE=0 reports without
 * annotating. FAILECHO_ENDPOINT defaults to https://failecho.com.
 */
"use strict";

const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const VERSION = require("../package.json").version;
const ADVICE_TIMEOUT_MS = 3000;
const REPORT_TIMEOUT_MS = 5000;
const INFER_WINDOW_MS = 120000;
const TRANSIENT = new Set(["rate_limit", "server_error", "timeout", "connection_error"]);

// Must match failecho_autoreport.ERROR_CLASSES -- a test holds them together,
// or evidence from the two languages would not join on one fingerprint.
const ERROR_CLASSES = [
  ["timeout", /time[d ]?\s?out|deadline exceeded|ETIMEDOUT/i],
  ["rate_limit", /\b429\b|rate.?limit|too many requests|quota/i],
  ["auth_error", /\b40[13]\b|unauthori[sz]ed|forbidden|permission denied|authenticat|invalid (api )?key/i],
  ["not_found", /\b404\b|not found|no such/i],
  // server_error first: "503 invalid upstream response" was validation_error
  ["server_error", /\b5\d\d\b|internal (server )?error|service unavailable|bad gateway|upstream/i],
  ["validation_error", /\b4(00|22)\b|invalid|validation|required|must be|schema/i],
  ["connection_error", /ECONN(REFUSED|RESET)|ENOTFOUND|EPIPE|connection (refused|reset|closed|error)|network|socket/i],
];
// MCP servers that wrap exactly one API host, by the name they report.
const DEFAULT_UPSTREAMS = {
  "github-mcp-server": "api.github.com",
  "github": "api.github.com",
  "mcp-server-github": "api.github.com",
};

/** 'github_*=api.github.com' -> ['github_*', 'api.github.com']; a bare host covers every tool. */
function parseUpstreams(specs) {
  const out = [];
  for (const spec of specs) {
    for (let part of String(spec || "").split(",")) {
      part = part.trim();
      if (!part) continue;
      const i = part.lastIndexOf("=");
      const [pattern, host] = i === -1 ? ["*", part] : [part.slice(0, i).trim() || "*", part.slice(i + 1).trim()];
      if (host) out.push([pattern, host.toLowerCase()]);
    }
  }
  return out;
}

/** fnmatchcase: * and ? only. */
function globMatch(pattern, name) {
  const re = new RegExp("^" + pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
  return re.test(name);
}

function upstreamFor(upstreams, service, tool) {
  for (const [pattern, host] of upstreams) if (globMatch(pattern, tool)) return host;
  return DEFAULT_UPSTREAMS[(service || "").toLowerCase()] || null;
}

const HTTP_CLASSES = new Set(["rate_limit", "auth_error", "not_found", "validation_error", "server_error"]);

function truthy(value, dflt) {
  if (value === undefined || value === null) return dflt;
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

/** The failure text of a tools/call response, or null if it succeeded. */
function errorText(message) {
  if ("error" in message) {
    const err = message.error || {};
    return `${err.code ?? ""} ${err.message ?? ""}`.trim() || "error";
  }
  const result = message.result;
  if (result && typeof result === "object" && result.isError) {
    const parts = (result.content || [])
      .filter((c) => c && typeof c === "object" && c.type === "text" && c.text)
      .map((c) => c.text);
    return parts.join(" ").slice(0, 2000) || "error";
  }
  return null;
}

/** One line from the network's answer, as failecho_autoreport.advice_text. */
function adviceText(answer) {
  if (!answer || typeof answer !== "object") return null;
  const rec = answer.recommendation || {};
  const tried = new Map();
  for (const a of answer.recovery_actions || []) {
    if (a && typeof a === "object") tried.set(a.action, a);
  }
  // the same error class on this service's other operations, pooled by the
  // server when the operation itself has nothing
  const pooled = new Map();
  for (const a of (answer.service_evidence && answer.service_evidence.recovery_actions) || []) {
    if (a && typeof a === "object") pooled.set(a.action, a);
  }
  if (rec.action === "skip") {
    return "FailEcho: skip -- nothing other agents tried recently has fixed this failure.";
  }
  if (rec.action) {
    const a = tried.get(rec.action) || pooled.get(rec.action) || {};
    const evidence = "successes" in a && "attempts" in a ? `, worked ${a.successes}/${a.attempts}` : "";
    const conf = typeof rec.confidence === "number" ? ` (evidence score ${rec.confidence.toFixed(2)})` : "";
    // everything the server qualified the recommendation with; dropping it
    // let a decaying action read as solid (review, 20 Sep)
    const marks = [];
    if (rec.decaying) marks.push("recent attempts are failing");
    if (rec.warning) marks.push(String(rec.warning).slice(0, 120));
    if (rec.scope && rec.scope !== "operation") marks.push(`evidence pooled across this ${rec.scope}`);
    if (rec.from_other_agents === false) marks.push("your own history only");
    const tail = marks.length ? ` -- ${marks.join("; ")}` : "";
    return `FailEcho: try ${rec.action}${evidence}${conf}${tail}.`;
  }
  for (const [lead, source] of [["other agents tried", tried], ["on this service's other operations, agents tried", pooled]]) {
    const seen = [...source.values()].filter((a) => "successes" in a && "attempts" in a && a.attempts);
    if (seen.length) {
      // stable, like Python's sort: equal rates keep the server's order
      seen.sort((x, y) => y.successes / y.attempts - x.successes / x.attempts);
      const parts = seen.slice(0, 3).map((a) => `${a.action} worked ${a.successes}/${a.attempts}`).join(", ");
      return `FailEcho: no clear fix yet; ${lead} ${parts}.`;
    }
  }
  return null;
}

/** The same response with the line added. Never removes anything. */
function annotate(message, line) {
  const out = { ...message };
  if (out.error && typeof out.error === "object") {
    out.error = { ...out.error, message: `${out.error.message ?? ""}\n${line}`.replace(/^\n/, "") };
  } else if (out.result && typeof out.result === "object") {
    out.result = { ...out.result, content: [...(out.result.content || []), { type: "text", text: line }] };
  }
  return out;
}

/**
 * A stable id for this installation, created on first use and kept in
 * ~/.local/state/failecho/installation -- the same file the Python wrapper
 * uses, so one machine is one reporter whichever client runs there. Without
 * it every process counted as another "unique reporter", which inflated the
 * one number the network is judged on (review, 20 Sep). Falls back to a
 * per-process id where nothing is writable.
 */
function installationId() {
  const base = process.env.XDG_STATE_HOME || path.join(os.homedir() || "/tmp", ".local", "state");
  const file = path.join(base, "failecho", "installation");
  try {
    const saved = fs.readFileSync(file, "utf8").trim();
    if (saved) return saved;
  } catch { /* not created yet */ }
  const fresh = `install-${crypto.randomBytes(8).toString("hex")}`;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, fresh, { mode: 0o600 });
    return fresh;
  } catch {
    return `anon-${crypto.randomBytes(6).toString("hex")}`;
  }
}

/** Reports and reads against the network. Never throws, never blocks a message. */
class Network {
  constructor() {
    this.endpoint = (process.env.FAILECHO_ENDPOINT || "https://failecho.com").replace(/\/+$/, "");
    this.headers = {
      "Content-Type": "application/json",
      "User-Agent": `failecho-mcp-node/${VERSION}`,
      "X-Reporter-ID": process.env.FAILECHO_REPORTER_ID || installationId(),
    };
    if (process.env.FAILECHO_OPERATOR_TOKEN) {
      this.headers.Authorization = `Bearer ${process.env.FAILECHO_OPERATOR_TOKEN}`;
    }
    this.fingerprints = new Map();
    // one chain, in order, so an outcome finds the fingerprint of the
    // failure it follows -- the Python wrapper's single worker, in Node
    this.chain = Promise.resolve();
  }

  async post(path, body, timeoutMs) {
    const res = await fetch(`${this.endpoint}${path}`, {
      method: "POST", headers: this.headers, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return text ? JSON.parse(text) : {};
  }

  enqueue(fn) {
    this.chain = this.chain.then(fn).catch(() => {});
  }

  observe(service, operation, outcome, latencyMs, errorType, errorCode) {
    // never an error_message field: the text is read locally to pick a class
    // and dropped, whatever the environment says (review, 20 Sep)
    const body = { service, operation, outcome };
    if (outcome === "failure") {
      body.error_type = errorType;
      if (errorCode) body.error_code = errorCode;
    }
    body.latency_ms = latencyMs;
    this.enqueue(async () => {
      const r = await this.post("/v1/observe", body, REPORT_TIMEOUT_MS);
      if (outcome === "failure" && r && r.fingerprint) {
        this.fingerprints.set(`${service} ${operation}`, r.fingerprint);
        this.fingerprints.set(`${service} ${operation} ${errorType} ${errorCode}`, r.fingerprint);
      }
    });
  }

  recovered(service, operation, action, successful, errorType, errorCode) {
    this.enqueue(async () => {
      const fp = this.fingerprints.get(`${service} ${operation} ${errorType} ${errorCode}`)
        || this.fingerprints.get(`${service} ${operation}`);
      if (!fp) return;   // nothing to attach it to; inventing one would be worse
      await this.post("/v1/outcome", { fingerprint: fp, action, successful: Boolean(successful) }, REPORT_TIMEOUT_MS);
    });
  }

  async check(service, operation, errorType, errorCode, timeoutMs = ADVICE_TIMEOUT_MS) {
    const body = { service, operation };
    if (errorType) body.error_type = errorType;
    if (errorCode) body.error_code = String(errorCode);
    try {
      return await this.post("/v1/query", body, timeoutMs);
    } catch {
      return null;
    }
  }

  flush(ms) {
    return Promise.race([this.chain, new Promise((r) => setTimeout(r, ms))]);
  }
}

/** Splits a byte stream into lines, keeping each line's exact bytes. */
function lineSplitter(onLine) {
  let buf = Buffer.alloc(0);
  return {
    push(chunk) {
      buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
      let i;
      while ((i = buf.indexOf(10)) !== -1) {
        onLine(buf.subarray(0, i + 1));
        buf = buf.subarray(i + 1);
      }
    },
    end() {
      if (buf.length) onLine(buf);
      buf = Buffer.alloc(0);
    },
  };
}

/** A remote Streamable HTTP server, shaped like a child process's pipes. */
class HttpUpstream {
  constructor(url, headers, onMessageLine, onEnd) {
    this.url = url;
    this.headers = headers;
    this.onLine = onMessageLine;
    this.onEnd = onEnd;
    this.sessionId = null;
    this.protocol = null;
    this.inflight = 0;
    this.closing = false;
  }

  requestHeaders() {
    const h = { "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...this.headers };
    if (this.sessionId) h["Mcp-Session-Id"] = this.sessionId;
    if (this.protocol) h["MCP-Protocol-Version"] = this.protocol;
    return h;
  }

  emit(obj, isInit) {
    for (const m of Array.isArray(obj) ? obj : [obj]) {
      if (isInit && m && m.result && typeof m.result === "object" && m.result.protocolVersion) {
        this.protocol = m.result.protocolVersion;
      }
      this.onLine(Buffer.from(JSON.stringify(m) + "\n"));
    }
  }

  write(line) {
    const text = line.toString("utf8").trim();
    if (!text) return;
    let msg = null;
    try { msg = JSON.parse(text); } catch { /* forwarded as sent */ }
    const rid = msg && typeof msg === "object" && "method" in msg ? msg.id : undefined;
    const isInit = !!(msg && msg.method === "initialize");
    this.inflight += 1;
    (async () => {
      try {
        const res = await fetch(this.url, { method: "POST", headers: this.requestHeaders(), body: text });
        if (isInit && res.headers.get("mcp-session-id")) this.sessionId = res.headers.get("mcp-session-id");
        if (!res.ok) {
          const detail = (await res.text()).slice(0, 200);
          if (rid !== undefined && rid !== null) {
            this.emit({ jsonrpc: "2.0", id: rid, error: { code: -32603, message: `HTTP ${res.status} from ${this.url}: ${detail}`.trim() } });
          }
          return;
        }
        const type = (res.headers.get("content-type") || "").toLowerCase();
        if (type.includes("text/event-stream") && res.body) {
          const decoder = new TextDecoder();
          let pending = "";
          let data = [];
          for await (const chunk of res.body) {
            pending += decoder.decode(chunk, { stream: true });
            let i;
            while ((i = pending.indexOf("\n")) !== -1) {
              const s = pending.slice(0, i).replace(/\r$/, "");
              pending = pending.slice(i + 1);
              if (s.startsWith("data:")) data.push(s.slice(5).trimStart());
              else if (s === "" && data.length) {
                try { this.emit(JSON.parse(data.join("\n")), isInit); } catch { /* not a message */ }
                data = [];
              }
            }
          }
          if (data.length) { try { this.emit(JSON.parse(data.join("\n")), isInit); } catch { /* not a message */ } }
        } else {
          const body = await res.text();
          if (body.trim()) { try { this.emit(JSON.parse(body), isInit); } catch { /* not a message */ } }
        }
      } catch (e) {
        if (rid !== undefined && rid !== null) {
          this.emit({ jsonrpc: "2.0", id: rid, error: { code: -32603, message: `cannot reach ${this.url}: ${e.cause?.code || e.name}: ${e.message}` } });
        }
      } finally {
        this.inflight -= 1;
        this.maybeEnd();
      }
    })();
  }

  end() {
    this.closing = true;
    this.maybeEnd();
  }

  async maybeEnd() {
    if (!this.closing || this.inflight > 0 || this.ended) return;
    this.ended = true;
    if (this.sessionId) {
      try {
        await fetch(this.url, { method: "DELETE", headers: this.requestHeaders(), signal: AbortSignal.timeout(5000) });
      } catch { /* the server expires it anyway */ }
    }
    this.onEnd(0);
  }
}

function run(argv, headers, upstreams) {
  upstreams = [...(upstreams || []), ...parseUpstreams([process.env.FAILECHO_UPSTREAM || ""])];
  const disabled = truthy(process.env.FAILECHO_DISABLED, false);
  const net = disabled ? null : new Network();
  const advise = !disabled && truthy(process.env.FAILECHO_ADVISE, true);
  let service = process.env.FAILECHO_SERVICE || null;
  const isUrl = /^https?:\/\//.test(argv[0]);
  const fallbackName = isUrl ? new URL(argv[0]).hostname.toLowerCase() : require("node:path").basename(argv[0]);
  const pending = new Map();      // request id (as JSON) -> {tool, started, digest} | null for initialize
  const retryable = new Map();    // tool + args hash -> {at, et, code}
  const advising = new Set();

  const toClient = (buf) => { try { process.stdout.write(buf); } catch { /* client gone */ } };

  function noteRequest(line) {
    let msg;
    try { msg = JSON.parse(line.toString("utf8")); } catch { return; }
    for (const m of Array.isArray(msg) ? msg : [msg]) {
      if (!m || typeof m !== "object" || !("id" in m) || !("method" in m)) continue;
      const key = JSON.stringify(m.id);
      if (m.method === "tools/call") {
        const p = m.params || {};
        if (typeof p.name === "string") {
          const digest = crypto.createHash("sha256").update(JSON.stringify(sortKeys(p.arguments || {}))).digest("hex");
          pending.set(key, { tool: p.name, started: Date.now(), digest });
        }
      } else if (m.method === "initialize") {
        pending.set(key, null);
      }
    }
  }

  function infer(svc, tool, digest, failedClass) {
    const key = `${tool} ${digest}`;
    const prev = retryable.get(key);
    retryable.delete(key);
    if (prev && Date.now() - prev.at <= INFER_WINDOW_MS) {
      net.recovered(svc, tool, "retry", failedClass === null, prev.et, prev.code);
    }
    if (failedClass && TRANSIENT.has(failedClass[0])) {
      if (retryable.size >= 256) retryable.delete(retryable.keys().next().value);
      retryable.set(key, { at: Date.now(), et: failedClass[0], code: failedClass[1] });
    }
  }

  function fromServer(line) {
    if (!net || pending.size === 0) return toClient(line);
    let msg;
    try { msg = JSON.parse(line.toString("utf8")); } catch { return toClient(line); }
    if (!msg || typeof msg !== "object" || Array.isArray(msg) || "method" in msg || !("id" in msg)) return toClient(line);
    const key = JSON.stringify(msg.id);
    if (!pending.has(key)) return toClient(line);
    const entry = pending.get(key);
    pending.delete(key);
    if (entry === null) {
      const info = (msg.result && msg.result.serverInfo) || {};
      if (!service && typeof info.name === "string") service = info.name;
      return toClient(line);
    }
    const svc = service || fallbackName;
    const latency = Date.now() - entry.started;
    const text = errorText(msg);
    if (text === null) {
      net.observe(svc, entry.tool, "success", latency);
      infer(svc, entry.tool, entry.digest, null);
      return toClient(line);
    }
    const [et, code] = classify(text);
    net.observe(svc, entry.tool, "failure", latency, et, code);
    infer(svc, entry.tool, entry.digest, [et, code]);
    if (!advise) return toClient(line);
    const job = (async () => {
      let out = line;
      try {
        const started = Date.now();
        let lineText = adviceText(await net.check(svc, entry.tool, et, code));
        const host = lineText ? null : upstreamFor(upstreams, svc, entry.tool);
        const left = ADVICE_TIMEOUT_MS - (Date.now() - started);
        if (host && host !== svc && left > 200) {
          const up = adviceText(await net.check(host, entry.tool, et, code, left));
          if (up) lineText = up.replace("FailEcho: ", `FailEcho (evidence from ${host}): `);
        }
        if (lineText) out = Buffer.from(JSON.stringify(annotate(msg, lineText)) + "\n");
      } catch { out = line; }
      toClient(out);
    })();
    advising.add(job);
    job.finally(() => advising.delete(job));
  }

  const serverLines = lineSplitter(fromServer);

  async function finish(code) {
    await Promise.race([Promise.all([...advising]), new Promise((r) => setTimeout(r, ADVICE_TIMEOUT_MS + 1000))]);
    if (net) await net.flush(2000);
    process.exit(code);
  }

  let upstream;
  if (isUrl) {
    upstream = new HttpUpstream(argv[0], headers, (buf) => serverLines.push(buf), (c) => { serverLines.end(); finish(c); });
  } else {
    let child;
    try {
      child = spawn(argv[0], argv.slice(1), { stdio: ["pipe", "pipe", "inherit"] });
    } catch (e) {
      process.stderr.write(`failecho-mcp proxy: cannot start '${argv[0]}': ${e.message}\n`);
      process.exit(127);
    }
    child.on("error", (e) => {
      process.stderr.write(`failecho-mcp proxy: cannot start '${argv[0]}': ${e.message}\n`);
      process.exit(127);
    });
    child.stdout.on("data", (c) => serverLines.push(c));
    child.on("close", (code, signal) => {
      serverLines.end();
      finish(code === null ? (signal ? 128 + (require("node:os").constants.signals[signal] || 0) : 1) : code);
    });
    child.stdin.on("error", () => {});
    for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => child.kill(sig));
    upstream = { write: (b) => child.stdin.write(b), end: () => child.stdin.end() };
  }

  const clientLines = lineSplitter((line) => {
    if (net) noteRequest(line);
    upstream.write(line);
  });
  process.stdin.on("data", (c) => clientLines.push(c));
  process.stdin.on("end", () => { clientLines.end(); upstream.end(); });
}

function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") {
    return Object.keys(v).sort().reduce((o, k) => { o[k] = sortKeys(v[k]); return o; }, {});
  }
  return v;
}

const USAGE = "usage: failecho-mcp proxy [--header 'Name: value' ...] [--upstream 'tool_*=api.host'] -- <server command> [args...]\n" +
              "       failecho-mcp proxy [--header 'Name: value' ...] -- https://host/mcp\n";

function main(argv) {
  const headers = {};
  const upstreamSpecs = [];
  while (argv.length && argv[0] !== "--") {
    if (argv[0] === "--header" && argv.length > 1 && argv[1].includes(":")) {
      const i = argv[1].indexOf(":");
      headers[argv[1].slice(0, i).trim()] = argv[1].slice(i + 1).trim();
      argv = argv.slice(2);
    } else if (argv[0] === "--upstream" && argv.length > 1) {
      upstreamSpecs.push(argv[1]);
      argv = argv.slice(2);
    } else break;
  }
  if (argv[0] === "--") argv = argv.slice(1);
  if (!argv.length) { process.stderr.write(USAGE); process.exit(2); }
  run(argv, headers, parseUpstreams(upstreamSpecs));
}

module.exports = { main, classify, adviceText, annotate, errorText, ERROR_CLASSES, parseUpstreams, upstreamFor,
                   installationId };
