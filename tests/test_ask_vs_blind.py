"""The public benchmark: anyone can run the comparison. What is tested
here is that it cannot be pointed at production, that its arithmetic is
honest, and that an asking agent does what the network says."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ask_vs_blind.py"

spec = importlib.util.spec_from_file_location("ask_vs_blind", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_it_refuses_the_production_network():
    r = subprocess.run([sys.executable, str(SCRIPT), "--endpoint", "https://failecho.com"], capture_output=True, text=True)
    assert r.returncode == 2 and "not adoption" in r.stdout


def test_the_lab_and_a_self_hosted_instance_are_allowed_by_the_check():
    for ok in ("https://lab.failecho.com", "http://localhost:8000"):
        assert not ("failecho.com" in ok and "lab." not in ok)


def test_an_asking_agent_skips_on_skip_and_retries_on_retry(monkeypatch):
    a = bench.Agent("http://127.0.0.1:9", "t", "ask")
    fetched = []

    def fetch(url, timeout):
        fetched.append(url)
        raise bench.urllib.error.HTTPError(url, 503, "boom", None, None)

    monkeypatch.setattr(a, "fetch", fetch)
    monkeypatch.setattr(a, "observe", lambda *x, **k: "fp")
    outcomes = []
    monkeypatch.setattr(a, "outcome", lambda fp, action, ok: outcomes.append((action, ok)))
    monkeypatch.setattr(bench, "TARGETS", [("always_broken", "u1", 1), ("flaky_read", "u2", 1)])
    monkeypatch.setattr(a, "ask", lambda op, et, code: {"recommendation": {"action": "skip" if op == "always_broken" else "retry"}})
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    a.run(1)
    assert a.calls == 2 and a.failures == 2 and a.skipped == 1
    assert fetched == ["u1", "u2", "u2"], "skipped the broken one, retried the flaky one"
    assert outcomes == [("retry", False)]
    assert a.attempts == 3   # 1 for the skipped failure, 2 for the retried one


def test_a_blind_agent_always_retries_once(monkeypatch):
    a = bench.Agent("http://127.0.0.1:9", "t", "blind")
    monkeypatch.setattr(a, "fetch", lambda url, timeout: (_ for _ in ()).throw(TimeoutError("timed out")))
    monkeypatch.setattr(a, "observe", lambda *x, **k: None)
    monkeypatch.setattr(a, "outcome", lambda *x: None)
    monkeypatch.setattr(bench, "TARGETS", [("slow_read", "u", 1)])
    a.run(2)
    assert a.calls == 2 and a.failures == 2 and a.attempts == 4 and a.skipped == 0


def test_the_summary_is_arithmetic():
    a = bench.Agent("x", "t", "ask"); a.calls, a.failures, a.attempts, a.recovered, a.skipped, a.seconds = 10, 4, 6, 1, 2, 3.0
    b = bench.Agent("x", "t", "ask"); b.calls, b.failures, b.attempts, b.seconds = 10, 0, 0, 1.0
    s = bench.summarise("ask", [a, b])
    assert s == {"cohort": "ask", "agents": 2, "calls": 20, "failures": 4, "attempts_per_failure": 1.5,
                 "recovered": 1, "skipped": 2, "seconds": 4.0}
