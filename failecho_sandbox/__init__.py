"""A throwaway microVM for code nobody reviewed.

The fleet's builder personas write Python and run it. Model-written code is
not something the production box executes, so it runs here instead: a
Firecracker VM booted for one persona run and killed at the end of it, with
a read-only root, a fresh scratch disk, no secrets in the environment, and
no route to anywhere except the allowlisting proxy in ``proxy.py``.

    with Sandbox() as vm:
        r = vm.python("print(2 + 2)")
        r = vm.run(["uv", "pip", "compile", "requirements.in"], files={"requirements.in": "requests\\n"})

What the host provides per boot, all under ``/run/failecho-sandbox/<id>/``:
the API socket, the vsock socket, a fresh ``scratch.ext4``, the VM config,
and the console log. ``close()`` kills the VM and removes the directory.

Boundaries, in order of how much they matter:

1. The VM. Model code runs in a different kernel with 1 vCPU and a fixed
   memory size. The host sees a process called ``firecracker`` and nothing
   the guest does reaches the host's filesystem.
2. The fence. The tap link carries traffic to one host port (the proxy) and
   nothing else; the guest has no default route. See ``proxy.py`` for the
   list of hosts the proxy will connect to.
3. The environment. The guest is handed the lab's URL and a reporter id.
   The provider keys, the operator tokens and the host's environment never
   cross the vsock.
4. One at a time. A lock file serialises boots, because there is one tap
   device and one box's worth of memory.

The Firecracker binary, kernel and root image are installed under
``/usr/local/bin`` and ``/var/lib/failecho-sandbox`` by hand (see
``docs/sandbox.md``); this module refuses to run without them rather than
fetching anything.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import time
import uuid

__all__ = ["Sandbox", "SandboxError", "Result", "available"]

FIRECRACKER = os.environ.get("FAILECHO_FIRECRACKER") or "/usr/local/bin/firecracker"
IMAGES = os.environ.get("FAILECHO_SANDBOX_IMAGES") or "/var/lib/failecho-sandbox"
RUN_DIR = os.environ.get("FAILECHO_SANDBOX_RUN") or "/run/failecho-sandbox"
TAP = os.environ.get("FAILECHO_SANDBOX_TAP") or "fctap0"
GUEST_MAC = "AA:FC:00:00:00:02"
VSOCK_PORT = 5000
MEM_MIB = int(os.environ.get("FAILECHO_SANDBOX_MEM_MIB") or 384)
SCRATCH_MIB = int(os.environ.get("FAILECHO_SANDBOX_SCRATCH_MIB") or 512)
BOOT_TIMEOUT = 20
KILL_GRACE = 3

BOOT_ARGS = ("console=ttyS0 reboot=k panic=1 pci=off nomodule random.trust_cpu=on loglevel=2 "
             "i8042.noaux i8042.nomux i8042.nopnp i8042.dumbkbd "
             "ro root=/dev/vda rootfstype=ext4 init=/usr/local/bin/sandbox-init")


class SandboxError(RuntimeError):
    """The VM did not boot, or the host side broke. Never a task's own failure."""


class Result(dict):
    """A task's outcome: exit, stdout, stderr, seconds, timed_out."""

    @property
    def ok(self) -> bool:
        return self.get("exit") == 0 and not self.get("timed_out")

    @property
    def exit(self) -> int:
        return int(self.get("exit", -1))

    @property
    def stdout(self) -> str:
        return self.get("stdout") or ""

    @property
    def stderr(self) -> str:
        return self.get("stderr") or ""


def available() -> str | None:
    """Why the sandbox cannot run here, or None if it can."""
    for path, what in ((FIRECRACKER, "firecracker binary"), (f"{IMAGES}/vmlinux", "guest kernel"),
                       (f"{IMAGES}/rootfs.ext4", "root image")):
        if not os.path.exists(path):
            return f"{what} missing at {path}"
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        return "/dev/kvm not accessible"
    if not os.path.exists(f"/sys/class/net/{TAP}"):
        return f"tap device {TAP} not present (failecho-sandbox-net.service)"
    return None


class Sandbox:
    def __init__(self, mem_mib: int = MEM_MIB, scratch_mib: int = SCRATCH_MIB):
        self.id = uuid.uuid4().hex[:12]
        self.dir = os.path.join(RUN_DIR, self.id)
        self.mem_mib, self.scratch_mib = mem_mib, scratch_mib
        self.proc: subprocess.Popen | None = None
        self._lock = None
        self.boot_seconds: float | None = None

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "Sandbox":
        self.boot()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def boot(self) -> None:
        why = available()
        if why:
            raise SandboxError(why)
        os.makedirs(RUN_DIR, exist_ok=True)
        self._lock = open(os.path.join(RUN_DIR, "lock"), "w")
        fcntl.flock(self._lock, fcntl.LOCK_EX)   # one VM at a time, one tap
        os.makedirs(self.dir, mode=0o700)
        started = time.monotonic()
        scratch = os.path.join(self.dir, "scratch.ext4")
        with open(scratch, "wb") as fh:
            fh.truncate(self.scratch_mib * 1024 * 1024)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-O", "^has_journal", scratch],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        config = {
            "boot-source": {"kernel_image_path": f"{IMAGES}/vmlinux", "boot_args": BOOT_ARGS},
            "drives": [
                {"drive_id": "rootfs", "path_on_host": f"{IMAGES}/rootfs.ext4", "is_root_device": True, "is_read_only": True},
                {"drive_id": "scratch", "path_on_host": scratch, "is_root_device": False, "is_read_only": False},
            ],
            "machine-config": {"vcpu_count": 1, "mem_size_mib": self.mem_mib, "smt": False},
            "network-interfaces": [{"iface_id": "eth0", "guest_mac": GUEST_MAC, "host_dev_name": TAP}],
            "vsock": {"guest_cid": 3, "uds_path": os.path.join(self.dir, "v.sock")},
        }
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        console = open(os.path.join(self.dir, "console.log"), "wb")
        # Firecracker applies its own seccomp filter to itself by default.
        self.proc = subprocess.Popen(
            [FIRECRACKER, "--api-sock", os.path.join(self.dir, "api.sock"),
             "--config-file", os.path.join(self.dir, "config.json")],
            stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},   # the VMM sees no keys either
            start_new_session=True,
        )
        console.close()
        deadline = time.monotonic() + BOOT_TIMEOUT
        last_err = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SandboxError(f"firecracker exited {self.proc.returncode} during boot: {self._console_tail()}")
            try:
                pong = self._exchange({"op": "ping"}, timeout=3)
                if pong.get("ok"):
                    self.boot_seconds = round(time.monotonic() - started, 2)
                    return
            except (OSError, SandboxError) as e:
                last_err = e
            time.sleep(0.15)
        self.close()
        raise SandboxError(f"guest did not answer within {BOOT_TIMEOUT}s: {last_err}; {self._console_tail()}")

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(KILL_GRACE)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        shutil.rmtree(self.dir, ignore_errors=True)
        if self._lock is not None:
            try:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
                self._lock.close()
            except OSError:
                pass
            self._lock = None

    def _console_tail(self) -> str:
        try:
            with open(os.path.join(self.dir, "console.log"), "rb") as fh:
                return fh.read()[-600:].decode("utf-8", "replace").strip()
        except OSError:
            return ""

    # -- tasks ---------------------------------------------------------------

    def run(self, argv: list[str], files: dict[str, str] | None = None, timeout: int = 60,
            env: dict[str, str] | None = None) -> Result:
        """Run argv as the guest's unprivileged user in /work. Never raises for
        the task's own failure; that is the result. Raises SandboxError only
        if the VM itself is gone."""
        if self.proc is None or self.proc.poll() is not None:
            raise SandboxError("sandbox is not running")
        req = {"argv": list(argv), "files": files or {}, "timeout": int(timeout), "env": env or {}}
        return Result(self._exchange(req, timeout=timeout + 15))

    def python(self, code: str, timeout: int = 60, env: dict[str, str] | None = None,
               autoreport: bool = False) -> Result:
        """Run a Python source string as /work/task.py.

        With autoreport=True it runs under ``python -m failecho_autoreport run``
        so the task's own HTTP calls are observed and reported to the endpoint
        in env (the lab). The wrapper prints its own one-line summary to stderr
        at exit, which the caller can read."""
        argv = ["python3", "-m", "failecho_autoreport", "run", "task.py"] if autoreport else ["python3", "task.py"]
        return self.run(argv, files={"task.py": code}, timeout=timeout, env=env)

    # -- vsock ---------------------------------------------------------------

    def _exchange(self, obj: dict, timeout: float) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(os.path.join(self.dir, "v.sock"))
            s.sendall(f"CONNECT {VSOCK_PORT}\n".encode())
            line = b""
            while not line.endswith(b"\n"):
                c = s.recv(1)
                if not c:
                    raise SandboxError("vsock closed during CONNECT")
                line += c
            if not line.startswith(b"OK"):
                raise SandboxError(f"vsock CONNECT refused: {line!r}")
            data = json.dumps(obj).encode()
            s.sendall(struct.pack(">I", len(data)) + data)
            head = _recv_exact(s, 4)
            (n,) = struct.unpack(">I", head)
            return json.loads(_recv_exact(s, n))
        finally:
            s.close()


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(min(65536, n - len(buf)))
        if not chunk:
            raise SandboxError("vsock closed mid-frame")
        buf += chunk
    return bytes(buf)
