"""Canonical error classes, so two agents describing one failure agree.

`error_type` is part of the fingerprint, and it is free text. The same HTTP 404
arrives as `not_found` from our own hook, `http_error` from a caller reading
the status off a response, `NotFound` from anything wrapping an SDK exception,
and `404` from someone in a hurry. Four fingerprints, one failure, nobody ever
matching anybody.

Three narrow rules, in order:

* **A synonym list.** `too_many_requests` is `rate_limit`. A list, not a
  heuristic, so every merge is a decision somebody made and can argue with.
* **Generic classes defer to the status code.** `http_error` says nothing on
  its own, so with a 404 beside it the class becomes `not_found`. A specific
  class is never overridden this way: if a caller says `auth_error` with a 500,
  that disagreement is theirs to keep and not ours to silently resolve.
* **A bare status code becomes its class.** `error_type: "404"` is `not_found`.

The vocabulary is the one the Claude Code hook emits, deliberately: the hook is
the largest single reporter and everything else should agree with it rather
than the other way round.

What is not done: `unauthorized` and `forbidden` stay apart. They are 401 and
403, they mean different things, and the fix for one is not the fix for the
other. Merging them would file evidence about credentials under a name that
means permissions.
"""

from __future__ import annotations

import re

#: The classes the hook can emit. Everything below resolves into this set or
#: is left exactly as the caller wrote it.
CANONICAL = frozenset({
    "rate_limit",
    "auth_error",
    "forbidden",
    "not_found",
    "validation_error",
    "server_error",
    "timeout",
    "connection_error",
    "conflict",
})

#: Different words for one class.
_SYNONYMS = {
    "ratelimit": "rate_limit",
    "rate_limited": "rate_limit",
    "rate-limited": "rate_limit",
    "throttled": "rate_limit",
    "too_many_requests": "rate_limit",
    "toomanyrequests": "rate_limit",
    "quota_exceeded": "rate_limit",

    "auth": "auth_error",
    "authentication_error": "auth_error",
    "authentication_failed": "auth_error",
    "unauthorized": "auth_error",
    "unauthenticated": "auth_error",
    "invalid_api_key": "auth_error",
    "permission_denied": "forbidden",
    "access_denied": "forbidden",

    "notfound": "not_found",
    "missing": "not_found",
    "no_such_file_or_directory": "not_found",
    "enoent": "not_found",

    "validation": "validation_error",
    "invalid_request": "validation_error",
    "invalid_argument": "validation_error",
    "bad_request": "validation_error",
    "unprocessable_entity": "validation_error",
    "schema_error": "validation_error",

    "internal_error": "server_error",
    "internal_server_error": "server_error",
    "service_unavailable": "server_error",
    "bad_gateway": "server_error",
    "upstream_error": "server_error",

    "timed_out": "timeout",
    "timeout_error": "timeout",
    "deadline_exceeded": "timeout",
    "etimedout": "timeout",

    "network_error": "connection_error",
    "econnreset": "connection_error",
    "econnrefused": "connection_error",
    "connection_refused": "connection_error",
    "connection_reset": "connection_error",
}

#: Classes that describe nothing. With a status code beside them the code wins.
_GENERIC = frozenset({
    "http_error", "httperror", "http", "error", "unknown", "exception",
    "failure", "failed", "api_error", "request_error", "tool_error",
})

#: Status code to class. The hook's own table, so the two agree.
_BY_STATUS = {
    "400": "validation_error",
    "401": "auth_error",
    "403": "forbidden",
    "404": "not_found",
    "408": "timeout",
    "409": "conflict",
    "422": "validation_error",
    "429": "rate_limit",
    "500": "server_error",
    "502": "server_error",
    "503": "server_error",
    "504": "timeout",
}

_SEPARATORS = re.compile(r"[\s\-]+")
_STATUS = re.compile(r"^[1-5]\d\d$")


def _status_class(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    code = str(error_code).strip()
    if _STATUS.match(code):
        return _BY_STATUS.get(code)
    return None


def canonical_error_type(
    error_type: str | None, error_code: str | None = None
) -> str:
    """Return the class this failure should be recorded and matched under.

    Idempotent, and never invents a class for something it does not recognise:
    an unknown value comes back trimmed and case-folded, exactly as before.
    """
    # Absent and empty are the same thing to a caller, so they resolve the
    # same way: no class of its own, but a status code can still supply one.
    name = "" if error_type is None else _SEPARATORS.sub(
        "_", str(error_type).strip().casefold()
    ).strip("_")
    if not name:
        return _status_class(error_code) or ""

    # "404" as the class itself.
    if _STATUS.match(name):
        return _BY_STATUS.get(name, name)

    name = _SYNONYMS.get(name, name)

    if name in _GENERIC:
        return _status_class(error_code) or name

    return name
