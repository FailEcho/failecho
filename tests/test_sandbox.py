"""The sandbox the builder personas run model-written code in.

None of this boots a VM: the suite has no KVM and should not need one. What
is tested is the fence -- the allowlist decision, the classification of a
failed run as local or shared, what the guest is and is not told -- because
those are the parts whose failure would matter, and the parts a VM boot would
not exercise anyway. ``python -m failecho_sandbox selftest`` covers the live
side on the box, and its checks are listed in ``failecho_sandbox/__main__.py``.
"""

from __future__ import annotations

import json
import re
import socket
import struct
import threading
from pathlib import Path

import pytest

from failecho_sandbox import BOOT_ARGS, Sandbox, SandboxError, available
from failecho_sandbox import proxy as fence
from failecho_fleet import builder

ROOT = Path(__file__).resolve().parents[1]
ALLOW = frozenset(fence.DEFAULT_ALLOW)


# -- the allowlist ----------------------------------------------------------


@pytest.mark.parametrize("method, target, expected", [
    ("CONNECT", "pypi.org:443", ("pypi.org", 443)),
    ("CONNECT", "PyPI.org:443", ("pypi.org", 443)),
    ("CONNECT", "files.pythonhosted.org", ("files.pythonhosted.org", 443)),
    ("GET", "http://pypi.org/simple/", ("pypi.org", 80)),
    ("CONNECT", "example.com:443", None),
    ("CONNECT", "evil.pypi.org:443", None),           # no suffix matching
    ("CONNECT", "pypi.org.evil.com:443", None),       # no prefix matching either
    ("CONNECT", "pypi.org:22", None),                 # TLS port only
    ("CONNECT", "pypi.org:8080", None),
    ("CONNECT", "172.16.0.1:8888", None),             # not itself
    ("CONNECT", "127.0.0.1:8089", None),              # not the lab's loopback port
    ("CONNECT", "failecho.com:443", None),            # production is not on the list
    ("GET", "http://example.com/", None),
    ("GET", "https://pypi.org/", None),               # plain-HTTP path takes no TLS URIs
    ("GET", "http://pypi.org:8080/", None),
    ("GET", "/simple/", None),                        # relative URI: not a proxy request
    ("TRACE", "http://pypi.org/", None),
    ("CONNECT", "pypi.org:443 extra", None),
])
def test_the_fence_decides_by_exact_host_and_port(method, target, expected):
    assert fence.decide(method, target, ALLOW) == expected


def test_the_default_allowlist_holds_no_production_and_no_provider():
    """The guest runs code nobody reviewed. It must not be able to reach the
    network that counts adoption, nor any host a key would be useful at."""
    for host in ALLOW:
        assert host != "failecho.com" and host != "www.failecho.com"
        assert not any(p in host for p in ("groq", "googleapis", "openrouter", "ollama", "anthropic", "openai"))


def test_the_allowlist_file_is_exact_hosts_only(tmp_path):
    f = tmp_path / "allow.txt"
    f.write_text("pypi.org   # index\n\n# comment\nAPI.GITHUB.COM\n")
    assert fence.load_allowlist(str(f)) == frozenset({"pypi.org", "api.github.com"})


def test_the_builders_doc_hosts_are_within_the_fence():
    """fetch_doc runs on the host, but its list must not be wider than what the
    guest could reach; otherwise the two personas' worlds differ."""
    assert set(builder.DOC_HOSTS) <= ALLOW


def test_the_proxy_answers_403_and_logs_the_host_only(capsys):
    """A refused CONNECT gets a 403 and a log line naming the host -- not the
    path, not the headers. Run against a real listener on loopback."""
    import asyncio

    async def scenario():
        p = fence.Proxy(ALLOW)
        server = await asyncio.start_server(p.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\nX-Secret: s3cr3t\r\n\r\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        server.close()
        await server.wait_closed()
        return line, p.denied

    line, denied = asyncio.run(scenario())
    assert line.startswith(b"HTTP/1.1 403") and denied == 1
    out = capsys.readouterr().out
    assert "deny CONNECT example.com" in out and "s3cr3t" not in out


# -- what the guest is told -------------------------------------------------


def test_the_kernel_mounts_root_read_only_and_boots_our_init():
    assert " ro " in f" {BOOT_ARGS} " and "init=/usr/local/bin/sandbox-init" in BOOT_ARGS


def test_the_vmm_process_gets_a_bare_environment():
    """The Firecracker process is started with PATH and nothing else: not the
    provider keys, not the operator tokens, nothing from the persona's
    environment. Checked in the source, since booting is not possible here."""
    src = (ROOT / "failecho_sandbox" / "__init__.py").read_text()
    m = re.search(r"subprocess\.Popen\((.*?)start_new_session=True", src, re.S)
    assert m and 'env={"PATH": "/usr/local/bin:/usr/bin:/bin"}' in m.group(1)


def test_the_guest_is_told_the_lab_or_nothing():
    on = builder.Builder("fleet-build-ask", "https://lab.example", fe=None).guest_env
    off = builder.Builder("fleet-build-ask", None, fe=None).guest_env
    assert on == {"FAILECHO_ENDPOINT": "https://lab.example", "FAILECHO_REPORTER_ID": "fleet-build-ask",
                  "FAILECHO_REPORT_SUCCESS": "1"}
    assert off == {"FAILECHO_DISABLED": "1"}
    for env in (on, off):
        assert not any(k.endswith(("_KEY", "_TOKEN")) for k in env)


def test_the_guest_init_ships_no_secrets_and_no_default_route():
    src = (ROOT / "failecho_sandbox" / "guest_init.py").read_text()
    assert "ip route add" not in src, "the guest must not be given a route"
    assert "172.16.0.1:8888" in src
    assert not re.search(r"(KEY|TOKEN|SECRET)\s*[=:]", src)


def test_the_guest_init_is_the_one_in_the_image_recipe():
    """The recipe copies guest_init.py into the image. If it were edited without
    rebuilding, the VM would run the old one; the recipe names the file so at
    least the drift is visible."""
    recipe = (ROOT / "scripts" / "build_sandbox_rootfs.sh").read_text()
    assert "failecho_sandbox/guest_init.py" in recipe and "sandbox-init" in recipe


def test_the_wire_protocol_round_trips():
    """The guest's framing, exercised on a loopback socket pair with the
    guest's own functions (imported, not booted)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("guest_init", ROOT / "failecho_sandbox" / "guest_init.py")
    guest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guest)
    a, b = socket.socketpair()
    payload = {"argv": ["python3", "-c", "print(1)"], "files": {"x.py": "print()"}, "timeout": 5}
    t = threading.Thread(target=guest.send_frame, args=(a, payload))
    t.start()
    got = guest.recv_frame(b)
    t.join()
    assert got == payload
    # an oversize frame is refused rather than buffered
    b.sendall(struct.pack(">I", 9 * 1024 * 1024))
    assert guest.recv_frame(a) is None


def test_the_guest_refuses_paths_outside_work():
    src = (ROOT / "failecho_sandbox" / "guest_init.py").read_text()
    assert 'if not path.startswith(WORK + "/")' in src


# -- local versus shared ----------------------------------------------------


@pytest.mark.parametrize("stderr, expected", [
    ('Traceback (most recent call last):\n  File "task.py", line 3\n    prnt(x)\nNameError: name \'prnt\' is not defined', ("local", None)),
    ("ModuleNotFoundError: No module named 'requets'", ("local", None)),
    ("ERROR: No matching distribution found for requets", ("local", None)),
    ("  × No solution found when resolving dependencies:\n  ╰─▶ Because urllib3<2 and requests==2.32 ...", ("local", None)),
    ("AssertionError: expected 'a-b' got 'a--b'", ("local", None)),
    ("urllib.error.HTTPError: HTTP Error 503: Service Unavailable", ("shared", "server_error")),
    ("urllib.error.HTTPError: HTTP Error 429: Too Many Requests", ("shared", "rate_limit")),
    ("urllib.error.HTTPError: HTTP Error 403: rate limit exceeded", ("shared", "rate_limit")),  # as the wrapper files it
    ("urllib.error.HTTPError: HTTP Error 403: Forbidden", ("shared", "auth_error")),
    ("requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.github.com', port=443): Read timed out.", ("shared", "timeout")),
    ("WARNING: Retrying (Retry(total=4...)) after connection broken by 'ReadTimeoutError(...)': /simple/rich/\n"
     "ERROR: Could not fetch URL https://pypi.org/simple/rich/: connection error", ("shared", "timeout")),
    ("error: Failed to fetch: `https://pypi.org/simple/requests/`\n  Caused by: error sending request for url", ("shared", "connection_error")),
    ("ERROR: Could not fetch URL https://files.pythonhosted.org/packages/x.whl: 503 Service Unavailable", ("shared", "server_error")),
])
def test_a_failed_run_is_filed_as_local_or_shared(stderr, expected):
    """Local bugs never reach the network; shared failures do. A local bug
    misfiled as shared is noise every other agent would query into."""
    assert builder.classify_output(stderr) == expected


def test_shared_index_failures_name_the_index():
    class Rec:
        def __init__(self):
            self.calls = []

        def record_failure(self, service, operation, exc, latency_ms=None, mutates=None):
            self.calls.append((service, operation, type(exc).__name__, str(exc)))

    fe = Rec()
    b = builder.Builder("fleet-build-ask", None, fe)

    class R(dict):
        ok = False
        exit = 1
        stdout = ""
        stderr = "ERROR: Could not fetch URL https://files.pythonhosted.org/packages/...: 503 Service Unavailable"

    out = b._account(R(), "pip")
    assert out["failure"] == "shared"
    assert fe.calls == [("files.pythonhosted.org", "pip install", "_Shared", "503 service unavailable")]
    assert b.shared_failures == [{"service": "files.pythonhosted.org", "error_type": "server_error"}]


def test_a_local_bug_is_counted_and_never_reported():
    class Rec:
        def record_failure(self, *a, **k):
            raise AssertionError("a local failure reached the reporter")

    b = builder.Builder("fleet-build-blind", None, Rec())

    class R(dict):
        ok = False
        exit = 1
        stdout = ""
        stderr = "Traceback ...\nKeyError: 'tag_name'"

    out = b._account(R(), None)
    assert out["failure"] == "local" and b.local_failures == 1 and b.shared_failures == []


def test_a_hang_is_local():
    b = builder.Builder("fleet-build-blind", None, None)

    class R(dict):
        ok = False
        exit = 124
        stdout = ""
        stderr = "timed out"   # the sandbox's word, not a network's

    b._account(R(timed_out=True), None)
    assert b.local_failures == 1 and not b.shared_failures


def test_without_a_vm_the_builder_says_so_instead_of_running_on_the_host():
    b = builder.Builder("fleet-build-ask", None, None)
    b.vm = None
    b.boot_error = "no kvm here"
    out = b.run_python("import os; os.system('id')")
    assert out == {"error": "sandbox unavailable: no kvm here"}


def test_available_explains_what_is_missing(monkeypatch):
    monkeypatch.setattr("failecho_sandbox.FIRECRACKER", "/nonexistent/firecracker")
    assert "firecracker binary missing" in (available() or "")


def test_boot_refuses_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("failecho_sandbox.FIRECRACKER", "/nonexistent/firecracker")
    monkeypatch.setattr("failecho_sandbox.RUN_DIR", str(tmp_path))
    with pytest.raises(SandboxError, match="missing"):
        Sandbox().boot()
    assert not list(tmp_path.iterdir()), "nothing should be created before the checks pass"


# -- the fleet's wiring -----------------------------------------------------


def test_the_builder_twins_share_a_provider_and_differ_only_in_asking():
    import failecho_fleet as F

    rows = {r[0]: r for r in F.PERSONAS if r[1] == "builder"}
    assert set(rows) == {"fleet-build-ask", "fleet-build-blind"} == F.BUILD_PERSONAS
    a, b = rows["fleet-build-ask"], rows["fleet-build-blind"]
    assert a[2] == b[2] and a[4] is b[4] and a[3] is True and b[3] is False


def test_the_first_sixteen_personas_did_not_change():
    """The builders were added to a running experiment. The rows that were
    already running must be byte-identical to what ran before."""
    import failecho_fleet as F

    before = [
        ("fleet-decor-ask-a", "decorator", "groq", True), ("fleet-decor-ask-b", "decorator", "ollama", True),
        ("fleet-decor-blind-a", "decorator", "groq", False), ("fleet-decor-blind-b", "decorator", "ollama", False),
        ("fleet-auto-ask-a", "auto", "openrouter", True), ("fleet-auto-ask-b", "auto", "gemini", True),
        ("fleet-auto-blind-a", "auto", "openrouter", False), ("fleet-auto-blind-b", "auto", "gemini", False),
        ("fleet-mcp-ask", "mcp", "groq", True), ("fleet-mcp-blind", "mcp", "groq", False),
        ("fleet-cron-a", "decorator", None, True), ("fleet-cron-b", "decorator", None, False),
        ("fleet-test-ask", "decorator", None, True), ("fleet-test-blind", "decorator", None, False),
        ("fleet-gh-ask", "decorator", None, True), ("fleet-gh-blind", "decorator", None, False),
    ]
    assert [r[:4] for r in F.PERSONAS[:16]] == before


def test_the_scoreboard_has_a_build_ledger(tmp_path, monkeypatch):
    import failecho_fleet as F

    monkeypatch.setattr(F, "REPORT_PATH", str(tmp_path / "fleet.json"))
    monkeypatch.setattr(F, "LAB_DB", "")
    state = {"runs": [
        {"at": "t", "reporter": "fleet-build-ask", "path": "builder", "provider": "groq", "asks": True, "tool_calls": 3,
         "failures": [], "build": {"vm_runs": 3, "local_failures": 2, "shared_failures": [{"service": "pypi.org", "error_type": "timeout"}],
                                   "task_done": True, "sandbox": "ok"}},
        {"at": "t", "reporter": "fleet-build-blind", "path": "builder", "provider": "groq", "asks": False, "tool_calls": 1,
         "failures": [], "build": {"vm_runs": 0, "local_failures": 0, "shared_failures": [], "task_done": False,
                                   "sandbox": "unavailable: x"}},
    ]}
    F.write_report(state)
    report = json.loads((tmp_path / "fleet.json").read_text())
    build = {b["cohort"]: b for b in report["build"]}
    assert build["build / ask"] == {"cohort": "build / ask", "vm_runs": 3, "local": 2, "shared": 1, "tasks_done": 1,
                                    "sandbox_down": 0, "shared_share": pytest.approx(1 / 3)}
    assert build["build / blind"]["sandbox_down"] == 1 and build["build / blind"]["shared_share"] is None
    assert {c["cohort"] for c in report["cohorts"]} >= {"build / ask", "build / blind"}


def test_builder_tasks_only_name_hosts_inside_the_fence():
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", " ".join(builder.BUILDER_TASKS)))
    assert hosts <= ALLOW, hosts - ALLOW
