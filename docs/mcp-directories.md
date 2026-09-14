# Listing FailEcho in MCP directories

Two kinds of listing, and they work completely differently:

- **The official registry** (`registry.modelcontextprotocol.io`) — a real
  publish step with cryptographic proof that you own `failecho.com`. Most
  other directories read from it.
- **Community directories** (Glama, PulseMCP, Smithery, mcp.so and friends) —
  mostly crawlers. Some pick up the official registry automatically, some index
  GitHub, a few take a submission form.

Do the official registry first. It is the one that propagates.

## Status, 2026-09-14

| Where | State |
|---|---|
| Official MCP registry | listed, `com.failecho/failecho` v0.2.0, active |
| Glama | claimed and hosted via the stdio relay |
| Smithery | published, `failecho/failecho` |
| mcpservers.org | listed under Development |
| PulseMCP | nothing to do -- submissions paused, ingests the registry |
| `awesome-mcp-servers` | PR #14162 open, not yet merged |
| Claude community marketplace | submitted 2026-09-14, pending review |
| `awesome-remote-mcp-servers` | not eligible -- requires OAuth or an API key |
| mcp.so | skipped, $39 |

Everything that can be done without waiting on someone else is done. What
remains is two review queues and a merge queue, none of which are worth
chasing.

---

## 1. Official MCP registry

`server.json` is already in the repository root and validates against the
registry schema:

```bash
mcp-publisher validate
```

### Step 1 — add the DNS TXT record (manual, Cloudflare)

The namespace `com.failecho/*` requires proving control of `failecho.com`. The
keypair lives at `/etc/failecho-mcp-registry.key` (root-only, never committed).

Print the record again at any time:

```bash
PUB=$(openssl pkey -in /etc/failecho-mcp-registry.key -pubout -outform DER \
      | tail -c 32 | base64)
echo "v=MCPv1; k=ed25519; p=$PUB"
```

In Cloudflare → `failecho.com` → **DNS → Records → Add record**:

| Field | Value |
|---|---|
| Type | `TXT` |
| Name | `@` |
| Content | `v=MCPv1; k=ed25519; p=<the base64 above>` |

TXT records are not proxied; there is no orange cloud to worry about.

Verify propagation:

```bash
dig +short TXT failecho.com @1.1.1.1 | grep MCPv1
```

### Step 2 — authenticate and publish

```bash
mcp-publisher login dns --domain failecho.com \
  --private-key "$(openssl pkey -in /etc/failecho-mcp-registry.key -text \
                   | grep -A3 'priv:' | tail -3 | tr -d ' :\n')"
mcp-publisher publish
```

Confirm:

```bash
curl "https://registry.modelcontextprotocol.io/v0/servers?search=com.failecho/failecho"
```

### Publishing an update

Bump `version` in `server.json` and run `mcp-publisher publish` again. The
version is the *listing* version, not the API version — bump it when the
description, endpoint or tool set changes.

---

## 2. Community directories

Most need nothing beyond the official registry entry. Smithery needed a
publish step, and Glama a claim plus a hosting configuration.

### Glama — claimed ✅, hosted through the stdio relay

Claimed 2026-09-11 with `glama.json` at the repository root (maintainer
`Fuyuki0`), plus a `v0.1.0` GitHub release so the "Glama release" check
passes.

Glama's hosting runner can only start a stdio server: its generated
Dockerfile wraps the start command in `mcp-proxy`, which talks to the child
process over stdin/stdout. FailEcho's server speaks Streamable HTTP, so
pointing the runner at `uvicorn` times out with
`MCP error -32001: Request timed out`. The container therefore runs the relay
in `failecho_mcp/`, which forwards to the shared network and stores nothing,
so a hosted instance can never become a separate, empty FailEcho.

Settings (Glama → server → Dockerfile configuration):

| Field | Value |
|---|---|
| Base image | `debian:trixie-slim` (either works; nothing needs a newer glibc) |
| Python version | default, or `3.12` |
| Build steps | `["uv sync"]` |
| CMD arguments | `["mcp-proxy", "--", "uv", "run", "failecho-mcp"]` |
| Placeholder parameters | empty |
| Pinned commit SHA | empty, meaning the latest commit |

Environment variables JSON schema:

```json
{
  "type": "object",
  "properties": {
    "FAILECHO_URL": {
      "type": "string",
      "description": "FailEcho network to relay to. Defaults to https://failecho.com/mcp."
    }
  },
  "required": []
}
```

Glama validates against its own clone of the repository. Push first and press
**Sync with GitHub** before saving, or a commit it has not fetched yet is
rejected as "Commit not found".

Verified locally in the same shape: a fresh tree, `uv sync`, then
`mcp-proxy -- uv run failecho-mcp` served all four tools and relayed a call.

"No recent usage" on the quality checklist measures real traffic. Nothing
fixes it except users.

### PulseMCP — automatic; submissions paused

As of 2026-09-11 PulseMCP has paused new submissions (notice dated
2026-09-03) and asks servers to publish to the official MCP registry instead,
which it ingests automatically when it reopens. FailEcho is already there
(`com.failecho/failecho`, status `active`), so there is nothing to do.

### Smithery — published ✅

Live at `failecho/failecho`. The full sequence, for reference and for
republishing:

```bash
export PATH="/root/.nvm/versions/node/v22.23.2/bin:$PATH"

# 1. Log in (browser). In a non-interactive shell this prints an auth_url.
npx -y @smithery/cli@latest auth login

# 2. Claim the brand namespace. This is a CLI command, not a dashboard step --
#    a Smithery "organization" is not required, the namespace is the identity.
npx -y @smithery/cli@latest namespace create failecho

# 3. Publish the remote endpoint.
npx -y @smithery/cli@latest mcp publish https://failecho.com/mcp \
  -n failecho/failecho
```

Publishing an external URL carries no metadata, so the listing starts with an
empty description and no icon. Fill it in with the update API:

```bash
TOKEN=$(npx -y @smithery/cli@latest auth whoami --full \
        | grep -oE 'smry_[A-Za-z0-9+/=_-]+' | head -1)

curl -X PATCH "https://api.smithery.ai/servers/failecho%2Ffailecho" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data @listing.json
```

where `listing.json` carries `displayName`, `description`, `homepage`,
`repositoryUrl`, `license` and `iconUrl`. Smithery connected to the endpoint
and enumerated all four tools on publish, which doubles as a live check that
the MCP surface works from outside.

### mcpservers.org — listed ✅

Live under **Development**, with the description:

> Live failure intelligence for AI agents. Check whether other agents are
> hitting the same tool failure and see which recovery actions actually worked
> before retrying.

The site returns 403 to scripted requests, so its state cannot be checked from
a shell -- open it in a browser to verify or edit.

### mcp.so — skipped, paid

Listing costs $39. Not worth it while the network has no real reporters and no
evidence that MCP directories send meaningful traffic. Revisit once the free
channels prove otherwise — then it is an informed $39 rather than a hopeful
one.

### awesome-mcp-servers — pull request open

PR [#14162](https://github.com/punkpeye/awesome-mcp-servers/pull/14162) adds
one line under **Monitoring**, alphabetically between `esp4ce/infra-mcp` and
`firecrawl/firecrawl-mcp-server`, in the list's own format (Glama score badge
and legend icons). Maintainers merge in batches.

The sister list `awesome-remote-mcp-servers` requires OAuth or API-key
authentication for inclusion. FailEcho is deliberately unauthenticated, so it
was not submitted there.

### Claude Code plugin — the community marketplace

FailEcho ships as a Claude Code plugin in `plugin/`, installable from our own
marketplace, which is `.claude-plugin/marketplace.json` in this repository:

```
/plugin marketplace add FailEcho/failecho
/plugin install failecho@failecho
```

Anthropic runs two public marketplaces, and only one of them takes
submissions:

- **`claude-plugins-official`** is curated by Anthropic at its own discretion.
  There is no application process, and the submission form does not add
  plugins to it. Nothing to do here.
- **`claude-community`** (`anthropics/claude-plugins-community`) is where
  third-party submissions land after review. Users add it with
  `/plugin marketplace add anthropics/claude-plugins-community`.

Submit through an in-app form:

- **Console**, for individual authors: <https://platform.claude.com/plugins/submit>
- **claude.ai**, which needs a Team or Enterprise organisation with directory
  management access:
  <https://claude.ai/admin-settings/directory/submissions/plugins/new>

Run `claude plugin validate ./plugin` first; the review pipeline runs the same
check plus automated safety screening. On 2026-09-12 the plugin manifest and
the marketplace manifest both passed, `--strict` included.

What the form is asking about, in one place:

| | |
|---|---|
| Plugin name | `failecho` — an immutable slug; renaming later needs a `renames` entry |
| Repository | <https://github.com/FailEcho/failecho>, public, MIT |
| Marketplace | `FailEcho/failecho` |
| Plugin directory | `plugin/` |
| What it installs | the FailEcho MCP server (`https://failecho.com/mcp`, no auth, no key) and `PostToolUse` / `PostToolUseFailure` hooks matching `mcp__.*` |
| What it sends | the server's public name, the tool name, a coarse error class and code, and latency. Never tool arguments, results, prompts, paths or session ids; error text only with `FAILECHO_HOOK_SEND_ERRORS=1`. Servers it cannot name publicly are skipped |
| Off switch | `FAILECHO_DISABLED=1` |

**Submitted 2026-09-14, pending review.** Check for the name in
`anthropics/claude-plugins-community`'s `.claude-plugin/marketplace.json`:

```bash
curl -s https://raw.githubusercontent.com/anthropics/claude-plugins-community/main/.claude-plugin/marketplace.json | grep -c failecho
```

After approval the plugin is pinned to a commit SHA in the community catalog
and CI bumps the pin as we push. The public catalog syncs nightly, so there is
a delay between approval and the plugin being installable; check for the name
in `anthropics/claude-plugins-community`'s `.claude-plugin/marketplace.json`.

## 3. What to write in every listing

Consistency matters more than cleverness: the same words in every directory is
what teaches a search engine that FailEcho is one entity.

**Name:** `failecho`

**Endpoint:** `https://failecho.com/mcp` (Streamable HTTP, no auth)

**Short description (under 100 chars, the registry's limit):**

```
Check what other agents hit the same tool failure — and what recovery worked. Ask before retrying.
```

**Longer description, where there is room:**

```
FailEcho is a live cross-agent failure intelligence network for AI agents and
autonomous software. When a tool, API or MCP operation fails, agents can check
whether other autonomous systems recently experienced the same failure and
which recovery actions actually worked, instead of retrying blindly.

Agents also contribute anonymous failure, success and recovery-outcome
telemetry. Metadata only: no prompts, tool arguments, tool results or user
content. No account, no API key, free during the public MVP.
```

**Tools** — always list all four, with the trigger sentence on the first:

```
check_tool_failure       Use after another tool, API or MCP operation fails, before retrying.
report_tool_failure      Share anonymous failure metadata with the network.
report_tool_success      Report a successful call so health rates have a denominator.
report_recovery_outcome  Tell the network whether a recovery action actually worked.
```

**Links:** `https://failecho.com` · `https://github.com/FailEcho/failecho` ·
`https://failecho.com/llms.txt`

---

## Why bother

Two reasons, in order:

1. **Distribution.** Directories are where agent developers look for tools. A
   listing is the difference between someone hearing about FailEcho and
   someone being able to use it in thirty seconds.
2. **Entity clarity.** Each listing is a page on a domain search engines
   already trust, using the word FailEcho next to `failecho.com`. That is what
   eventually stops Google reading the name as the phrase "fail echo".
