"""/demo: one failure going through the loop, with the real output.

The proof used to sit halfway down the homepage behind a disclosure. It now
has a page, and the thing that makes it worth reading -- that the output is
real and reproducible -- is what these tests protect.
"""

from __future__ import annotations

from pathlib import Path

DEMO = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "demo.html").read_text()


def test_demo_is_served(client):
    response = client.get("/demo")
    assert response.status_code == 200
    assert "See it happen" in response.text


def test_page_metadata(client):
    body = client.get("/demo").text
    assert "<title>See it happen — FailEcho</title>" in body
    assert "/demo" in body and 'rel="canonical"' in body


def test_the_story_is_four_steps_and_states_the_payoff():
    for step in ("A tool call fails", "Other agents already tried",
                 "FailEcho returns", "Agent B skips the retry"):
        assert step in DEMO
    assert "Agent B recovered using evidence it never generated itself." in DEMO


def test_the_evidence_is_shown_not_summarised():
    """Both actions and their counts, so the recommendation is checkable."""
    assert "retry" in DEMO and "0 / 5 worked" in DEMO
    assert "refresh_schema" in DEMO and "5 / 5 worked" in DEMO


def test_the_output_is_real_and_reproducible():
    assert "6ed9ef705ff4037af2c977306b8b9f92" in DEMO, "the real fingerprint"
    assert "python examples/live_agent/run_demo.py" in DEMO
    assert "confidence 0.57" in DEMO


def test_it_leads_somewhere():
    assert 'href="/setup"' in DEMO
    assert 'href="/network"' in DEMO


def test_the_page_needs_no_javascript():
    """Static proof: nothing here depends on a script running. The one script
    adds a copy button to the terminal box and nothing else."""
    assert DEMO.count("<script") == 1
    assert "app.js" in DEMO
    assert "<script>" not in DEMO, "no inline script"
    assert "refresh_schema" in DEMO, "the output is in the markup, not fetched"
