#!/usr/bin/python3
"""PID 1 inside the sandbox VM. Installed as /usr/local/bin/sandbox-init.

The microVM boots this instead of a real init. It mounts the pseudo
filesystems and the scratch disk, gives the guest its one link-local
address, and then serves tasks over vsock until the host kills the VM.

A task is argv plus files plus a timeout. It runs as the unprivileged
``runner`` user in ``/work``, which is the fresh scratch disk the host made
for this boot. The root filesystem is mounted read-only by the kernel, so
nothing a task does survives the VM, and nothing it does is visible to the
next VM.

The guest has no default route. The only network the host offers it is a
CONNECT proxy at 172.16.0.1:8888 with a domain allowlist, and that address
is where every HTTP client in here is told to go through the proxy
variables in the task environment. A task that ignores them gets "Network
is unreachable" at once, which is the point: the fence is on the host, this
side only makes the honest path the easy one.

Wire protocol on vsock port 5000, one connection per task:

    4 bytes big-endian length, then JSON, both directions.

    -> {"argv": [...], "files": {"relative/path": "text"}, "env": {...},
        "timeout": 60}
    <- {"exit": 0, "stdout": "...", "stderr": "...", "seconds": 1.2,
        "timed_out": false}

    -> {"op": "ping"}      <- {"ok": true, "python": "3.12.3"}

stdout and stderr are each cut to 32 KB. Nothing here sends anything
anywhere; the host is the only peer.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import socket
import struct
import subprocess
import sys
import time

PORT = 5000
WORK = "/work"
OUTPUT_CAP = 32 * 1024
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
PROXY = "http://172.16.0.1:8888"


def sh(*argv: str) -> None:
    subprocess.call(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def mount_all() -> None:
    os.makedirs("/proc", exist_ok=True)
    sh("mount", "-t", "proc", "proc", "/proc")
    sh("mount", "-t", "sysfs", "sysfs", "/sys")
    if not os.path.exists("/dev/vda"):
        sh("mount", "-t", "devtmpfs", "devtmpfs", "/dev")
    for d in ("/tmp", "/run", "/var/tmp"):
        sh("mount", "-t", "tmpfs", "-o", "size=64m,mode=1777", "tmpfs", d)
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists("/dev/vdb"):
        sh("mount", "-t", "ext4", "/dev/vdb", WORK)
    else:
        sh("mount", "-t", "tmpfs", "-o", "size=128m", "tmpfs", WORK)
    os.makedirs(f"{WORK}/home", exist_ok=True)
    sh("chown", "-R", "runner:runner", WORK)


def network_up() -> None:
    sh("ip", "link", "set", "lo", "up")
    sh("ip", "addr", "add", "172.16.0.2/30", "dev", "eth0")
    sh("ip", "link", "set", "eth0", "up")
    # No default route on purpose: the proxy is on-link, everything else is
    # unreachable rather than merely filtered.


def base_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": f"{WORK}/home",
        "USER": "runner",
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PIP_BREAK_SYSTEM_PACKAGES": "1",
        "UV_HTTP_TIMEOUT": "30",
        "UV_NO_PROGRESS": "1",
        "UV_CACHE_DIR": f"{WORK}/.uv-cache",
        "HTTP_PROXY": PROXY, "HTTPS_PROXY": PROXY,
        "http_proxy": PROXY, "https_proxy": PROXY,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }


def reap() -> None:
    try:
        while os.waitpid(-1, os.WNOHANG)[0] > 0:
            pass
    except ChildProcessError:
        pass


def run_task(req: dict) -> dict:
    argv = req.get("argv") or ["python3", "-c", "print('no argv')"]
    files = req.get("files") or {}
    timeout = max(1, min(int(req.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    env = base_env()
    for k, v in (req.get("env") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and k.isidentifier():
            env[k] = v
    for rel, text in files.items():
        path = os.path.normpath(os.path.join(WORK, rel))
        if not path.startswith(WORK + "/"):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text if isinstance(text, str) else "")
        os.chown(path, 1000, 1000)
    started = time.monotonic()
    timed_out = False
    try:
        p = subprocess.Popen(argv, cwd=WORK, env=env, user="runner", group="runner",
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    except OSError as e:
        return {"exit": 127, "stdout": "", "stderr": f"cannot start: {e}", "seconds": 0.0, "timed_out": False}
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)   # stray grandchildren, if any
        except ProcessLookupError:
            pass
        reap()
    return {
        "exit": p.returncode if not timed_out else 124,
        "stdout": out[-OUTPUT_CAP:].decode("utf-8", "replace"),
        "stderr": err[-OUTPUT_CAP:].decode("utf-8", "replace"),
        "seconds": round(time.monotonic() - started, 2),
        "timed_out": timed_out,
    }


def recv_frame(conn: socket.socket) -> dict | None:
    head = b""
    while len(head) < 4:
        chunk = conn.recv(4 - len(head))
        if not chunk:
            return None
        head += chunk
    (n,) = struct.unpack(">I", head)
    if n > 8 * 1024 * 1024:
        return None
    body = bytearray()
    while len(body) < n:
        chunk = conn.recv(min(65536, n - len(body)))
        if not chunk:
            return None
        body += chunk
    return json.loads(bytes(body))


def send_frame(conn: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode()
    conn.sendall(struct.pack(">I", len(data)) + data)


def serve() -> None:
    srv = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    srv.bind((socket.VMADDR_CID_ANY, PORT))
    srv.listen(1)
    while True:
        conn, _ = srv.accept()
        try:
            conn.settimeout(MAX_TIMEOUT + 30)
            req = recv_frame(conn)
            if not req:
                continue
            if req.get("op") == "ping":
                send_frame(conn, {"ok": True, "python": platform.python_version()})
            else:
                send_frame(conn, run_task(req))
        except Exception as e:  # noqa: BLE001 - PID 1 must not die on a bad frame
            try:
                send_frame(conn, {"exit": 125, "stdout": "", "stderr": f"sandbox: {e}", "seconds": 0.0, "timed_out": False})
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()
            reap()


def main() -> None:
    mount_all()
    network_up()
    print("sandbox-init: ready", flush=True)
    serve()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"sandbox-init: fatal {e}", file=sys.stderr, flush=True)
        time.sleep(2)
    os._exit(0)  # PID 1 exiting panics the kernel; the host is killing us anyway
