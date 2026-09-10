# Security

FailEcho is a public, unauthenticated service that collects failure metadata
from autonomous systems. Two kinds of issue matter here, and they are worth
separating.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: on the repository, go to
**Security → Report a vulnerability**. It creates a private advisory visible
only to maintainers.

There is no security email address yet. When one exists it will be listed
here; until then the GitHub advisory flow is the only channel, and inventing an
address that nobody reads would be worse than saying so.

Expect an acknowledgement within a few days. This is a small project run by a
small number of people, not a vendor with an on-call rotation.

## What counts as a vulnerability

- Any way to read data FailEcho promises never to store (see below).
- Any way to recover a raw reporter identifier from stored hashes.
- Any way to make one reporter's evidence dominate a recommendation beyond the
  documented per-reporter cap.
- Remote code execution, SQL injection, path traversal, or authentication
  bypass on the admin surfaces (there is currently no admin surface).
- Denial of service that a single client can cause despite the documented rate
  limits.

## What is known and intentional

These are documented design decisions, not vulnerabilities:

- **No authentication.** Anyone may report anything. The per-reporter evidence
  cap and the write rate limit raise the cost of poisoning; they do not make it
  impossible. A distributed campaign from many IPs would still get through.
- **The rate limiter is in-process.** One worker, one budget. It resets on
  restart and is not shared across processes.
- **Reporter identifiers are self-asserted.** A reporter ID is a salted hash of
  a string the caller chose. It proves nothing about identity; it only lets the
  network tell two reporters apart.
- **`X-Reporter-Kind: demo` is trusted.** Self-labelling can only *downgrade* a
  report out of adoption metrics, never promote one, so trusting it is safe.

## Data FailEcho must never store

If you find any path by which any of these reach the database or the logs,
that is a vulnerability, and an important one:

prompts · model messages · tool arguments · tool results · request bodies ·
response bodies · HTTP headers · cookies · API keys · tokens · customer names ·
emails · any user content

The raw `error_message` is normalized at the edge and discarded; only the
normalized form is stored. Credential-shaped substrings are redacted before
storage rather than categorised.

## Running your own instance

FailEcho is MIT licensed and runs on one small VPS. If your failure metadata
cannot leave your network, self-host it: `FIN_PUBLIC_URL`, a salt of your own,
and nothing reports outward.
