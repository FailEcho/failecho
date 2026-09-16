"""The fence: an HTTP CONNECT proxy with a domain allowlist.

The sandbox VM has no route to the internet. Its only way out is this
process, listening on the host end of the tap link, and this process only
opens connections to hosts on the allowlist. Everything the fence permits is
therefore written down in one place, and the firewall rule on the tap
interface makes this port the only thing the guest can reach at all.

What it handles:

* ``CONNECT host:443`` -- a TLS tunnel to an allowed host. The proxy never
  sees inside it.
* ``GET http://host/...`` and the other plain-HTTP methods with an absolute
  URI -- forwarded to port 80 of an allowed host.

Anything else is answered 403 and closed. Denials are logged by host name
only; paths and headers are not logged, because a path is where identifiers
live and the guest is running code nobody reviewed.

    python -m failecho_sandbox.proxy            # 172.16.0.1:8888, default list
    FAILECHO_SANDBOX_ALLOW=/etc/failecho-sandbox/allow.txt   # one host per line

The list is exact host names. There are no wildcards on purpose: a wildcard
is how "the package index" quietly becomes "anything on that CDN".
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time

LISTEN_HOST = os.environ.get("FAILECHO_SANDBOX_PROXY_HOST", "172.16.0.1")
LISTEN_PORT = int(os.environ.get("FAILECHO_SANDBOX_PROXY_PORT") or 8888)
ALLOW_FILE = os.environ.get("FAILECHO_SANDBOX_ALLOW") or ""
MAX_CONNECTIONS = 16
IDLE_TIMEOUT = 120
CONNECT_TIMEOUT = 15

#: What a build task legitimately needs: package indexes, their file hosts,
#: GitHub's API and raw files, the language docs, the test service, and the
#: lab so the task's own HTTP calls can be reported. Not production, not the
#: model providers (those keys never enter the guest), not anything with a
#: login on it.
DEFAULT_ALLOW = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "api.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "docs.python.org",
    "peps.python.org",
    "packaging.python.org",
    "developer.mozilla.org",
    "httpbingo.org",
    "lab.failecho.com",
)

_HOSTPORT = re.compile(r"^([A-Za-z0-9.-]+)(?::(\d{1,5}))?$")
_ABSURI = re.compile(r"^https?://([^/:]+)(?::(\d{1,5}))?(/.*)?$", re.I)


def load_allowlist(path: str = ALLOW_FILE) -> frozenset[str]:
    if not path:
        return frozenset(DEFAULT_ALLOW)
    hosts = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                hosts.add(line)
    return frozenset(hosts)


def decide(method: str, target: str, allow: frozenset[str]) -> tuple[str, int] | None:
    """(host, port) to connect to, or None to refuse. Pure, so it is testable."""
    method = method.upper()
    if method == "CONNECT":
        m = _HOSTPORT.match(target)
        if not m:
            return None
        host, port = m.group(1).lower(), int(m.group(2) or 443)
        if port != 443:
            return None
    else:
        m = _ABSURI.match(target)
        if not m or method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            return None
        host, port = m.group(1).lower(), int(m.group(2) or 80)
        if target.lower().startswith("https://") or port != 80:
            return None
    if host.rstrip(".") not in allow:
        return None
    return host, port


class Proxy:
    def __init__(self, allow: frozenset[str]):
        self.allow = allow
        self.active = 0
        self.allowed = 0
        self.denied = 0

    def log(self, msg: str) -> None:
        print(f"[sandbox-proxy] {msg}", flush=True)

    async def pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if self.active >= MAX_CONNECTIONS:
            writer.close()
            return
        self.active += 1
        upstream_w = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3:
                raise ValueError("bad request line")
            method, target = parts[0], parts[1]
            where = decide(method, target, self.allow)
            if where is None:
                self.denied += 1
                self.log(f"deny {method} {_host_only(target)} from {peer[0] if peer else '?'}")
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            host, port = where
            try:
                upstream_r, upstream_w = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            self.allowed += 1
            if method.upper() == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                # rewrite the absolute URI to a path, keep the headers, drop
                # proxy-only ones; plain HTTP is rare here (pip and uv use TLS)
                m = _ABSURI.match(target)
                path = m.group(3) or "/"
                out = [f"{method} {path} HTTP/1.1"]
                for h in lines[1:]:
                    if h and not h.lower().startswith(("proxy-connection:", "proxy-authorization:")):
                        out.append(h)
                out.append("Connection: close")
                upstream_w.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
                await upstream_w.drain()
            await asyncio.gather(self.pipe(reader, upstream_w), self.pipe(upstream_r, writer))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, ConnectionError, asyncio.LimitOverrunError):
            pass
        finally:
            self.active -= 1
            for w in (writer, upstream_w):
                if w is not None:
                    try:
                        w.close()
                    except Exception:  # noqa: BLE001
                        pass


def _host_only(target: str) -> str:
    m = _ABSURI.match(target) or _HOSTPORT.match(target)
    return (m.group(1) if m else "?")[:80]


async def serve(host: str = LISTEN_HOST, port: int = LISTEN_PORT, allow: frozenset[str] | None = None) -> None:
    proxy = Proxy(allow or load_allowlist())
    server = await asyncio.start_server(proxy.handle, host, port, limit=64 * 1024)
    proxy.log(f"listening on {host}:{port}, {len(proxy.allow)} hosts allowed")
    last = time.monotonic()
    async with server:
        while True:
            await asyncio.sleep(300)
            if time.monotonic() - last >= 3600:
                proxy.log(f"hour: allowed={proxy.allowed} denied={proxy.denied}")
                last = time.monotonic()


def main() -> int:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
