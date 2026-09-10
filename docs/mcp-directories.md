# Listing FailEcho in MCP directories

Two kinds of listing, and they work completely differently:

- **The official registry** (`registry.modelcontextprotocol.io`) — a real
  publish step with cryptographic proof that you own `failecho.com`. Most
  other directories read from it.
- **Community directories** (Glama, PulseMCP, Smithery, mcp.so and friends) —
  mostly crawlers. Some pick up the official registry automatically, some index
  GitHub, a few take a submission form.

Do the official registry first. It is the one that propagates.

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

| Directory | How to get listed |
|---|---|
| **Glama** | Indexes GitHub automatically. Having `server.json` in the repo root and the MCP topic set is usually enough. Claim the listing afterwards. |
| **PulseMCP** | Crawls the official registry and GitHub. Has a submission form for anything it missed. |
| **Smithery** | Submission via their site; oriented toward hosted/remote servers, which FailEcho is. |
| **mcp.so** | Submission form. |
| **awesome-mcp-servers lists** | Pull requests against the relevant GitHub list, under the category that fits — reliability/observability tooling. |

Search for the current submission URL rather than trusting a link from this
file; these sites change often enough that a hardcoded URL here would rot.

---

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
