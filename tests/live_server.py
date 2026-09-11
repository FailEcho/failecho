"""A real FailEcho server on a real port, for tests that cross the network."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass(frozen=True)
class LiveServer:
    base: str
    workdir: Path


@contextmanager
def running_failecho(**extra_env: str):
    """Start uvicorn with its own database; yield once /health answers."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    workdir = Path(tempfile.mkdtemp(prefix="fin-live-"))
    env = {
        **os.environ,
        "FIN_DATABASE_URL": f"sqlite+aiosqlite:///{workdir / 'live.db'}",
        "FIN_PUBLIC_URL": base,
        "FIN_DASHBOARD_CACHE_SECONDS": "0",
        "FIN_REPORTER_SALT": "live-test-salt",
        **extra_env,
    }
    log_path = workdir / "server.log"
    log = log_path.open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    log.flush()
                    raise RuntimeError(
                        "FailEcho did not start:\n" + log_path.read_text()
                    ) from None
                time.sleep(0.2)
        yield LiveServer(base=base, workdir=workdir)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()
