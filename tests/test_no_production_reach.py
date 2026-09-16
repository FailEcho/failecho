"""The test suite cannot reach production. Proven, not assumed."""

from __future__ import annotations

import pytest


def test_resolving_production_from_a_test_raises():
    import socket

    with pytest.raises(RuntimeError, match="never touch production"):
        socket.getaddrinfo("failecho.com", 443)


@pytest.mark.parametrize("lib", ["urllib", "requests", "httpx"])
def test_every_http_client_is_blocked_from_production(lib):
    """The guard sits below all three, so a client that was monkeypatched
    into reporting cannot get a report out regardless of which one it uses."""
    if lib == "urllib":
        import urllib.request
        with pytest.raises(Exception) as e:
            urllib.request.urlopen("https://failecho.com/v1/stats", timeout=5)
    elif lib == "requests":
        import requests
        with pytest.raises(Exception) as e:
            requests.get("https://failecho.com/v1/stats", timeout=5)
    else:
        import httpx
        with pytest.raises(Exception) as e:
            httpx.get("https://failecho.com/v1/stats", timeout=5)
    assert "never touch production" in str(e.value) or "never touch production" in repr(e.value.__cause__ or e.value.__context__ or "")


def test_the_default_reporter_endpoint_in_tests_is_a_dead_local_port():
    import os

    from failecho_autoreport import FailEcho

    assert os.environ["FAILECHO_ENDPOINT"] == "http://127.0.0.1:9"
    assert FailEcho().endpoint == "http://127.0.0.1:9"


def test_auto_does_not_patch_on_import():
    """The incident. Importing must be inert; only enable() or `run` patches.
    Checked in a fresh interpreter, because tests in this process have
    already called enable() on purpose."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import requests, urllib.request, httpx;"
         "o=(requests.Session.request, urllib.request.OpenerDirector.open, httpx.Client.send);"
         "import failecho_autoreport.auto;"
         "print((requests.Session.request, urllib.request.OpenerDirector.open, httpx.Client.send) == o)"],
        capture_output=True, text=True, timeout=60, cwd="/root/agentwebsite",
    )
    assert out.stdout.strip() == "True", out.stderr
