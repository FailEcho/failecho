#!/usr/bin/env node
/**
 * FailEcho over stdio, for MCP hosts that can only start a local process.
 *
 * A relay, not a second FailEcho: it has no database and stores nothing. Every
 * message is forwarded to the shared network and the reply handed straight
 * back, so this serves the same four tools, with the same descriptions and the
 * same evidence, as pointing a client at the URL directly.
 *
 * No dependencies on purpose. `npx` should fetch one small thing and start,
 * because a person deciding whether to try this will not wait.
 */
"use strict";

// `failecho-mcp proxy -- <server>`: FailEcho in front of another MCP server
// (see proxy.js). Loaded only here, so the plain relay never runs it.
if (process.argv[2] === "proxy") {
  require("./proxy.js").main(process.argv.slice(3));
  return;
}

const DEFAULT_URL = "https://failecho.com/mcp";
const VERSION = require("../package.json").version;
const TIMEOUT_MS = 15000;

const url = process.env.FAILECHO_URL || DEFAULT_URL;

const headers = {
  "Content-Type": "application/json",
  Accept: "application/json, text/event-stream",
  "User-Agent": `failecho-mcp-node/${VERSION}`,
};
if (process.env.FAILECHO_REPORTER_ID) {
  headers["X-Reporter-ID"] = process.env.FAILECHO_REPORTER_ID;
}
if (process.env.FAILECHO_OPERATOR_TOKEN) {
  headers["X-FailEcho-Operator"] = process.env.FAILECHO_OPERATOR_TOKEN;
}

const log = (m) => process.stderr.write(`failecho-mcp: ${m}\n`);

function write(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

/** Server-sent events carry the JSON-RPC message in `data:` lines. */
function fromEventStream(text) {
  const payloads = [];
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("data:")) {
      const body = line.slice(5).trim();
      if (body) payloads.push(body);
    }
  }
  return payloads;
}

async function forward(message) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(message),
      signal: controller.signal,
    });

    // Notifications are accepted with no body; there is nothing to hand back.
    if (response.status === 202 || response.status === 204) return;

    const text = await response.text();
    if (!text.trim()) return;

    const type = response.headers.get("content-type") || "";
    const chunks = type.includes("text/event-stream")
      ? fromEventStream(text)
      : [text];

    for (const chunk of chunks) {
      try {
        write(JSON.parse(chunk));
      } catch {
        // The length, not the content. A malformed reply is still a
        // protocol message, and the first 120 characters of one is
        // whatever happened to be at the front of it.
        log(`ignored an unparseable reply (${chunk.length} bytes)`);
      }
    }
  } catch (error) {
    // A relay that dies on a network blip takes the host's session with it.
    // Answer the request instead, so the agent sees a failure it can handle.
    const reason = error && error.name === "AbortError"
      ? `no response from ${url} within ${TIMEOUT_MS} ms`
      : `${(error && error.message) || error}`;
    log(reason);
    if (message && message.id !== undefined && message.id !== null) {
      write({
        jsonrpc: "2.0",
        id: message.id,
        error: { code: -32000, message: `FailEcho unreachable: ${reason}` },
      });
    }
  } finally {
    clearTimeout(timer);
  }
}

// One message per line, and requests are answered in the order they arrive so
// a slow call cannot reorder the stream underneath the host.
let buffer = "";
let queue = Promise.resolve();

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      // Same reasoning: a malformed line from the host may be carrying
      // tool arguments, and this process has no business copying them
      // into the host's logs.
      log(`ignored an unparseable line from the host (${line.length} bytes)`);
      continue;
    }
    queue = queue.then(() => forward(message));
  }
});

process.stdin.on("end", () => queue.then(() => process.exit(0)));
log(`relaying to ${url}`);
