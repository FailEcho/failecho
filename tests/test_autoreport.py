"""The wrapper that reports without a model deciding to.

The Claude Code hook covers Claude Code. This covers everything else, and it
runs inside somebody else's program, so most of what is tested here is what it
must *not* do: raise, block, change a return value, or count one failure twice.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from failecho_autoreport import FailEcho, classify


class Recorder(FailEcho):
    """A client that keeps the bodies instead of sending them."""

    def __init__(self, **kwargs):
        super().__init__(endpoint="http://127.0.0.1:9", **kwargs)
        self.bodies: list[dict] = []

    def _post(self, body):  # noqa: D102 - the whole point is not to post
        self.bodies.append(body)


def drained(client: FailEcho) -> None:
    assert client.flush(timeout=5), "reports did not drain"


# -- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "message, expected",
    [
        ("429 rate limit exceeded", ("rate_limit", "429")),
        ("403 Forbidden", ("auth_error", "403")),
        ("deadline exceeded", ("timeout", None)),
        ("422 validation failed: field required", ("validation_error", "422")),
        ("ECONNREFUSED", ("connection_error", None)),
        ("503 bad gateway upstream", ("server_error", "503")),
        ("something nobody has a pattern for", ("error", None)),
    ],
)
def test_classification_matches_the_hook(message, expected):
    """Both paths must produce the same error_type for the same failure, or
    evidence reported through the hook and evidence reported through the
    wrapper will not join up on the same fingerprint."""
    assert classify(RuntimeError(message)) == expected


def test_the_hook_and_the_wrapper_share_one_table():
    """Not "look similar" -- the same patterns, checked against the file."""
    import re
    from pathlib import Path

    hook = Path("plugin/hooks/failecho_hook.py").read_text()
    block = hook[hook.index("ERROR_CLASSES = ("):]
    block = block[: block.index("\n)")]
    names = re.findall(r'\("([a-z_]+)", re\.compile', block)

    from failecho_autoreport import ERROR_CLASSES

    assert [n for n, _ in ERROR_CLASSES] == names


# -- it must not change the program it wraps -------------------------------


def test_the_exception_comes_back_out_unchanged():
    client = Recorder()

    @client.watch(service="api.github.com", operation="create_issue")
    def boom():
        raise RuntimeError("429 rate limit exceeded")

    with pytest.raises(RuntimeError, match="429 rate limit exceeded"):
        boom()


def test_the_return_value_comes_back_unchanged():
    client = Recorder()
    sentinel = object()

    @client.watch(service="api.github.com")
    def fine():
        return sentinel

    assert fine() is sentinel


def test_async_callables_are_wrapped_as_async():
    client = Recorder()

    @client.watch(service="api.github.com", operation="async_op")
    async def boom():
        raise TimeoutError("deadline exceeded")

    with pytest.raises(TimeoutError):
        asyncio.run(boom())
    drained(client)
    assert client.bodies[0]["error_type"] == "timeout"


def test_a_dead_endpoint_costs_the_caller_nothing():
    """The reporting is on a worker thread, so an unreachable FailEcho must
    not add its timeout -- or any wait at all -- to the caller's failure."""
    client = FailEcho(endpoint="http://127.0.0.1:9", reporter_id="dead")

    @client.watch(service="x.example", operation="y")
    def boom():
        raise ValueError("500 internal error")

    started = time.monotonic()
    with pytest.raises(ValueError):
        boom()
    assert (time.monotonic() - started) < 0.5, "the caller waited on the network"


def test_a_reporting_failure_is_never_raised_at_the_host():
    client = FailEcho(endpoint="http://127.0.0.1:9", reporter_id="dead")
    client.record_failure("x.example", "y", RuntimeError("boom"))
    client.flush(timeout=3)
    assert client.failed >= 0  # reached here at all: nothing propagated


# -- what it sends ---------------------------------------------------------


def test_error_text_is_not_sent_unless_asked_for():
    client = Recorder()
    client.record_failure("api.github.com", "create_issue",
                          RuntimeError("429 for user tok_SECRET"))
    drained(client)
    body = client.bodies[0]
    assert "error_message" not in body, "the error text left the process by default"
    assert body["error_type"] == "rate_limit" and body["error_code"] == "429"


def test_error_text_is_sent_when_asked_for():
    client = Recorder(send_errors=True)
    client.record_failure("api.github.com", "create_issue", RuntimeError("429 nope"))
    drained(client)
    assert "429 nope" in client.bodies[0]["error_message"]


def test_successes_are_reported_because_rates_need_a_denominator():
    client = Recorder()

    @client.watch(service="api.github.com", operation="create_issue")
    def fine():
        return 1

    fine()
    drained(client)
    assert client.bodies[0]["outcome"] == "success"


def test_successes_can_be_turned_off():
    client = Recorder(report_success=False)
    client.record_success("api.github.com", "create_issue")
    drained(client)
    assert client.bodies == []


def test_disabled_sends_nothing_at_all():
    client = Recorder(enabled=False)
    client.record_failure("api.github.com", "x", RuntimeError("429"))
    client.record_success("api.github.com", "x")
    drained(client)
    assert client.bodies == []


def test_it_does_not_report_failecho_to_failecho():
    """A loop, and not shared infrastructure anybody else is calling either."""
    client = Recorder()
    client.record_failure("failecho.com", "query", RuntimeError("503"))
    drained(client)
    assert client.bodies == []


# -- the two bugs found by running it --------------------------------------


def test_one_call_reports_once_even_when_a_tool_has_two_layers():
    """A LangChain StructuredTool exposes `_run` and `func`, and `_run` calls
    `func`. Wrapping both counted every failure twice, which is worse than not
    counting it: the network cannot tell a double count from two agents
    agreeing with each other."""
    client = Recorder()

    class TwoLayerTool:
        name = "create_issue"

        def func(self):
            raise RuntimeError("422 invalid")

        def _run(self):
            return self.func()

    tool = TwoLayerTool()
    client.wrap([tool], service="github-mcp")

    with pytest.raises(RuntimeError):
        tool._run()
    drained(client)
    assert len(client.bodies) == 1, f"reported {len(client.bodies)} times for one call"


def test_flush_waits_for_the_request_in_flight():
    """The worker takes an item off the queue before sending it, so an empty
    queue is not a drained one -- a short script that exits on `empty()` loses
    its last report."""
    slow = Recorder()
    original = slow._post

    def lingering(body):
        time.sleep(0.3)
        original(body)

    slow._post = lingering
    slow.record_failure("api.github.com", "x", RuntimeError("429"))
    assert slow.flush(timeout=5)
    assert slow.bodies, "flush returned before the report was actually sent"


def test_the_queue_is_bounded_so_a_retry_storm_cannot_grow_it():
    from failecho_autoreport import MAX_QUEUE

    client = Recorder()
    client._queue.maxsize = 4
    # never start the worker: fill the queue and watch it refuse more
    for _ in range(20):
        client._submit({"service": "x", "operation": "y", "outcome": "failure"})
    assert client.dropped > 0, "the queue grew without bound"
    assert MAX_QUEUE > 0


# -- what running it against real frameworks turned up ---------------------


def test_a_tool_whose_name_is_on_its_metadata_is_still_wrapped():
    """LangChain puts the name on the tool. LlamaIndex puts it on
    tool.metadata, and looking only at `.name` skipped every LlamaIndex tool
    silently -- leaving the caller believing reporting was on."""
    client = Recorder()

    class Meta:
        name = "create_issue"

    class LlamaishTool:
        metadata = Meta()

        def __init__(self):
            self._fn = self._boom

        @staticmethod
        def _boom():
            raise RuntimeError("403 forbidden")

        def call(self):
            return self._fn()

    tool = LlamaishTool()
    client.wrap([tool], service="github-mcp")
    assert client.wrapped >= 1, "the tool was skipped"

    with pytest.raises(RuntimeError):
        tool.call()
    drained(client)
    assert len(client.bodies) == 1
    assert client.bodies[0]["error_type"] == "auth_error"


def test_wrapping_nothing_is_visible_rather_than_silent():
    """A tool it cannot wrap must show up in a counter. Reporting that is
    quietly off is worse than reporting that is loudly absent."""
    client = Recorder()

    class Opaque:
        pass

    client.wrap([Opaque()], service="x")
    assert client.wrapped == 0 and client.unwrapped == 1


# -- recovery outcomes -----------------------------------------------------


def test_an_outcome_attaches_to_the_failure_before_it():
    """Failure rates say a call is broken; only an outcome says what to do
    about it, which is the half another agent can use. The fingerprint arrives
    on the observe response, so it is resolved at send time -- the failure may
    still be on the queue when recovered() is called, and one worker draining
    in order is what makes that safe."""
    client = Recorder()
    outcomes: list[dict] = []

    def fake_observe(body):
        client.bodies.append(body)
        client._fingerprints[(body["service"], body["operation"])] = "fp-1"

    def fake_outcome(body):
        key = (body["service"], body["operation"])
        outcomes.append({**body, "fingerprint": client._fingerprints.get(key)})

    client._post = fake_observe
    client._post_outcome = fake_outcome

    client.record_failure("github-mcp", "create_issue", RuntimeError("422 invalid"))
    client.recovered("github-mcp", "create_issue", "refresh_schema", True)
    drained(client)

    assert len(outcomes) == 1
    assert outcomes[0]["fingerprint"] == "fp-1", "the outcome lost its failure"
    assert outcomes[0]["action"] == "refresh_schema"
    assert outcomes[0]["successful"] is True


def test_an_orphan_outcome_is_dropped_not_misattached():
    """With no failure to attach to, inventing a fingerprint would put the
    outcome on the wrong failure -- worse than losing it."""
    client = Recorder()
    client.recovered("never-seen", "op", "guess", True)
    drained(client)
    assert client.unmatched == 1
    assert client.bodies == []


# -- our own agents must never count as adoption ----------------------------


def test_operator_token_is_sent_as_bearer_and_only_when_set():
    """An agent we run that reports without this is stored as source: agent
    and counted as a stranger adopting us. With it, the server files the
    report as first_party. Bearer rather than the custom header because it
    survives hosts that filter unknown header names."""
    plain = FailEcho(endpoint="http://127.0.0.1:9", reporter_id="x")
    assert "Authorization" not in plain._headers()

    ours = FailEcho(endpoint="http://127.0.0.1:9", reporter_id="x", operator_token="tok-1")
    assert ours._headers()["Authorization"] == "Bearer tok-1"


def test_operator_token_comes_from_the_environment_too(monkeypatch):
    monkeypatch.setenv("FAILECHO_OPERATOR_TOKEN", "env-tok")
    assert FailEcho(endpoint="http://127.0.0.1:9")._headers()["Authorization"] == "Bearer env-tok"


def test_mutates_is_sent_only_when_declared():
    client = Recorder()

    @client.watch(service="api.example", operation="create_thing", mutates=True)
    def write():
        return 1

    @client.watch(service="api.example", operation="get_thing")
    def read():
        return 1

    write(); read()
    drained(client)
    by_op = {b["operation"]: b for b in client.bodies}
    assert by_op["create_thing"]["mutates"] is True
    assert "mutates" not in by_op["get_thing"], "undeclared must stay undeclared, not default to a guess"


def test_wrap_takes_per_tool_declarations():
    client = Recorder()

    class T:
        def __init__(self, name):
            self.name = name
            self.func = lambda: 1

    tools = [T("create_issue"), T("get_issue")]
    client.wrap(tools, service="github-mcp", mutates={"create_issue": True, "get_issue": False})
    for t in tools:
        t.func()
    drained(client)
    by_op = {b["operation"]: b.get("mutates") for b in client.bodies}
    assert by_op == {"create_issue": True, "get_issue": False}
