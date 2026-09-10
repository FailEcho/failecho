"""A tiny local tool server that has just shipped a breaking contract change.

This stands in for the "normal tool" an agent calls. It is deterministic: the
same request always produces the same response, so the demo is reproducible and
needs no LLM, no network access and no credentials.

The story it tells:

    v2 of the API accepted   {"repository": ..., "body": ...}
    v3 of the API expects    {"repository": ..., "content": ...}

An agent holding a cached v2 tool schema keeps sending ``body`` and gets:

    422 validation_error
    Repository 123456 rejected field body

The fix is not "retry" -- retrying sends the same stale field and fails again.
The fix is to refresh the tool schema and resend with ``content``. That is
exactly the kind of thing the failure network is for: no single agent can tell
those two apart from one 422, but a hundred agents' recovery outcomes can.

Run standalone:

    python examples/live_agent/tool_server.py        # http://127.0.0.1:8765
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TOOL_NAME = "demo-issues-api"
OPERATION = "create_issue"

#: The contract the server is on. v3 renamed ``body`` to ``content``.
CONTRACT_VERSIONS = {
    "2.0.0": {"content_field": "body"},
    "3.0.0": {"content_field": "content"},
}

app = FastAPI(title="Demo issues API", version="3.0.0")

#: Mutable so a caller can demo healthy / broken / recovered states.
state: dict[str, Any] = {"version": "3.0.0", "issue_counter": 0}


def _content_field() -> str:
    return CONTRACT_VERSIONS[state["version"]]["content_field"]


@app.get("/schema", summary="Current tool schema (this is what refresh_schema fetches)")
async def schema() -> dict[str, Any]:
    field = _content_field()
    return {
        "service": TOOL_NAME,
        "operation": OPERATION,
        "version": state["version"],
        # A short hash of the accepted field set, exactly what an agent would
        # send to the failure network as schema_hash.
        "schema_hash": "v2body" if field == "body" else "v3cont",
        "fields": ["repository", field],
        "renamed": {} if field == "body" else {"body": "content"},
    }


@app.get("/state", summary="Which contract the server is serving")
async def get_state() -> dict[str, Any]:
    return {"version": state["version"], "content_field": _content_field()}


@app.post("/state", summary="Switch contract version (demo control)")
async def set_state(payload: dict[str, Any]) -> dict[str, Any]:
    version = str(payload.get("version", "3.0.0"))
    if version not in CONTRACT_VERSIONS:
        return JSONResponse(
            {"error": "unknown_version", "known": sorted(CONTRACT_VERSIONS)},
            status_code=400,
        )
    state["version"] = version
    return {"version": state["version"], "content_field": _content_field()}


@app.post("/issues", summary="Create an issue")
async def create_issue(request: Request) -> JSONResponse:
    payload = await request.json()
    repository = payload.get("repository")
    expected = _content_field()
    stale = "body" if expected == "content" else "content"

    if repository is None:
        return JSONResponse(
            {
                "error": "validation_error",
                "message": "Repository is required",
            },
            status_code=422,
        )

    if stale in payload:
        # The failure the whole demo hangs on. The repository id makes every
        # agent's message textually different while meaning the same thing --
        # which is what the network's normalizer exists to collapse.
        return JSONResponse(
            {
                "error": "validation_error",
                "message": (
                    f"Repository {repository} rejected field {stale}: "
                    f'field "{stale}" is no longer accepted, use "{expected}"'
                ),
                "schema_version": state["version"],
            },
            status_code=422,
        )

    if expected not in payload:
        return JSONResponse(
            {
                "error": "validation_error",
                "message": (
                    f"Repository {repository} is missing required field {expected}"
                ),
            },
            status_code=422,
        )

    state["issue_counter"] += 1
    return JSONResponse(
        {
            "id": state["issue_counter"],
            "repository": repository,
            "schema_version": state["version"],
        },
        status_code=201,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
