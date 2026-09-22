"""A reporter identity that can be proven.

Three things have to hold, or signing is worse than not offering it: the
arithmetic must be right (RFC 8032, and the client and server copies must
agree), a signature that does not verify must be refused rather than quietly
downgraded, and an unsigned reporter must keep working exactly as before.

The fourth thing is a claim we must not make. Signing proves the holder of a
key, nothing more. Keys are free, so this is not Sybil resistance and not a
count of people, and the wording that ships with it says so.
"""

from __future__ import annotations

import binascii
import json
import os
import time

import pytest

from app.core import ed25519 as server_ed25519
from app.core import identity as server_identity
from failecho_autoreport import _ed25519 as client_ed25519
from failecho_autoreport.identity import Identity, key_path, signed_material

# RFC 8032, section 7.1, tests 1 and 2.
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb882"
     "1590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1"
     "e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


def _hex(value: str) -> bytes:
    return binascii.unhexlify(value)


# -- the arithmetic ----------------------------------------------------------


@pytest.mark.parametrize("seed,public,message,signature", VECTORS)
def test_the_client_matches_rfc_8032(seed, public, message, signature):
    seed, public, message, signature = (_hex(seed), _hex(public), _hex(message), _hex(signature))
    assert client_ed25519.public_key(seed) == public
    assert client_ed25519.sign(seed, message) == signature
    assert client_ed25519.verify(public, message, signature)


@pytest.mark.parametrize("seed,public,message,signature", VECTORS)
def test_the_server_matches_rfc_8032(seed, public, message, signature):
    public, message, signature = (_hex(public), _hex(message), _hex(signature))
    assert server_ed25519.verify(public, message, signature)
    # Both paths, so a host with `cryptography` and a host without agree.
    assert server_ed25519._slow_verify(public, message, signature)


def test_the_two_copies_agree_on_every_answer():
    """The client signs and the server verifies, in different files. They are
    duplicated on purpose -- neither package may depend on the other -- so the
    only thing keeping them honest is this."""
    seed = client_ed25519.generate_seed()
    public = client_ed25519.public_key(seed)
    for message in (b"", b"one", b"x" * 1000, bytes(range(256))):
        signature = client_ed25519.sign(seed, message)
        assert server_ed25519.verify(public, message, signature)
        assert server_ed25519._slow_verify(public, message, signature)
        assert not server_ed25519.verify(public, message + b"!", signature)


def test_a_tampered_signature_is_never_accepted():
    seed = client_ed25519.generate_seed()
    public = client_ed25519.public_key(seed)
    signature = bytearray(client_ed25519.sign(seed, b"hello"))
    for index in (0, 31, 32, 63):
        broken = bytearray(signature)
        broken[index] ^= 0x01
        assert not server_ed25519.verify(public, b"hello", bytes(broken))
    assert not server_ed25519.verify(public, b"hello", b"")
    assert not server_ed25519.verify(b"short", b"hello", bytes(signature))


def test_a_non_canonical_scalar_is_refused():
    """S must be reduced mod L. An unreduced S verifies under a naive check
    and lets one signature be rewritten into another for the same message."""
    seed = client_ed25519.generate_seed()
    public = client_ed25519.public_key(seed)
    signature = client_ed25519.sign(seed, b"hello")
    s = int.from_bytes(signature[32:], "little") + client_ed25519.L
    mutated = signature[:32] + s.to_bytes(32, "little")
    assert not server_ed25519.verify(public, b"hello", mutated)


# -- the key on disk ---------------------------------------------------------


def test_the_key_is_created_once_and_kept_private(tmp_path):
    path = str(tmp_path / "identity.key")
    first = Identity.load_or_create(path)
    assert first is not None
    assert first.reporter_id.startswith("ed25519:")
    assert oct(os.stat(path).st_mode)[-3:] == "600", "a private key must not be world readable"

    again = Identity.load_or_create(path)
    assert again.reporter_id == first.reporter_id, "a restart must be the same reporter"


def test_an_unwritable_home_costs_a_signature_not_a_crash(tmp_path):
    """Read-only containers exist. Reporting unsigned beats not reporting, and
    a key held only in memory would be a new identity on every restart -- the
    inflation this mechanism exists to prevent."""
    blocked = tmp_path / "nope"
    blocked.write_text("not a directory")
    assert Identity.load_or_create(str(blocked / "sub" / "identity.key")) is None


def test_the_key_path_follows_the_installation_id_not_the_project(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("FAILECHO_IDENTITY_KEY", raising=False)
    assert key_path() == str(tmp_path / "failecho" / "identity.key")


# -- over HTTP ---------------------------------------------------------------


def _signed_post(client, path, payload, identity, timestamp=None, body_override=None):
    body = json.dumps(payload).encode()
    stamp = int(time.time()) if timestamp is None else timestamp
    material = signed_material(stamp, "POST", path, body_override if body_override is not None else body)
    signature = client_ed25519.sign(identity.seed, material)
    import base64
    return client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Reporter-ID": identity.reporter_id,
            "X-Reporter-Timestamp": str(stamp),
            "X-Reporter-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
        },
    )


@pytest.fixture
def identity(tmp_path):
    return Identity.load_or_create(str(tmp_path / "id.key"))


OBSERVATION = {
    "service": "api.github.com",
    "operation": "create_issue",
    "outcome": "failure",
    "error_type": "rate_limit",
    "error_code": "429",
}


def test_a_signed_report_is_accepted_and_the_reporter_is_recorded(client, identity):
    response = _signed_post(client, "/v1/observe", OBSERVATION, identity)
    assert response.status_code == 200 and response.json()["accepted"] is True

    stats = client.get("/v1/stats").json()
    assert stats["verified_reporters"] == 1


def test_the_same_reporter_is_counted_once_however_often_it_signs(client, identity):
    for _ in range(3):
        assert _signed_post(client, "/v1/observe", OBSERVATION, identity).status_code == 200
    assert client.get("/v1/stats").json()["verified_reporters"] == 1


def test_an_unsigned_report_still_works_and_claims_nothing(client):
    response = client.post("/v1/observe", json=OBSERVATION,
                           headers={"X-Reporter-ID": "plain-old-string"})
    assert response.status_code == 200
    assert client.get("/v1/stats").json()["verified_reporters"] == 0


def test_a_bad_signature_is_refused_rather_than_downgraded(client, identity):
    """The dangerous outcome is a reporter that believes it is signing, is
    not, and is counted among the anonymous crowd it was trying to leave."""
    body = json.dumps(OBSERVATION).encode()
    response = client.post(
        "/v1/observe", content=body,
        headers={"Content-Type": "application/json",
                 "X-Reporter-ID": identity.reporter_id,
                 "X-Reporter-Timestamp": str(int(time.time())),
                 "X-Reporter-Signature": "not-a-signature"},
    )
    assert response.status_code == 400
    assert client.get("/v1/stats").json()["verified_reporters"] == 0


def test_a_signature_does_not_travel_to_another_body(client, identity):
    """Capture a signed request, change what it says, and it stops verifying."""
    response = _signed_post(client, "/v1/observe", OBSERVATION, identity,
                            body_override=b'{"service":"something.else"}')
    assert response.status_code == 400


def test_an_old_signature_is_refused(client, identity):
    stale = int(time.time()) - (server_identity.MAX_SKEW_SECONDS + 60)
    assert _signed_post(client, "/v1/observe", OBSERVATION, identity, timestamp=stale).status_code == 400


def test_signing_under_somebody_elses_id_proves_nothing(client, identity):
    """The id must be the key. Otherwise a signature says only that *some*
    key signed, which is worth exactly nothing."""
    other = Identity(client_ed25519.generate_seed())
    body = json.dumps(OBSERVATION).encode()
    stamp = int(time.time())
    import base64
    signature = client_ed25519.sign(other.seed, signed_material(stamp, "POST", "/v1/observe", body))
    response = client.post(
        "/v1/observe", content=body,
        headers={"Content-Type": "application/json",
                 "X-Reporter-ID": identity.reporter_id,
                 "X-Reporter-Timestamp": str(stamp),
                 "X-Reporter-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("=")},
    )
    assert response.status_code == 400


def test_a_signature_on_a_plain_string_id_is_refused(client, identity):
    body = json.dumps(OBSERVATION).encode()
    stamp = int(time.time())
    import base64
    signature = client_ed25519.sign(identity.seed, signed_material(stamp, "POST", "/v1/observe", body))
    response = client.post(
        "/v1/observe", content=body,
        headers={"Content-Type": "application/json",
                 "X-Reporter-ID": "just-a-name",
                 "X-Reporter-Timestamp": str(stamp),
                 "X-Reporter-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("=")},
    )
    assert response.status_code == 400


def test_a_signed_query_stores_nothing(client, identity):
    """Reads stay reads. A signature on one must not turn it into a record of
    who asked what."""
    response = _signed_post(client, "/v1/query",
                            {"service": "api.github.com", "operation": "create_issue"}, identity)
    assert response.status_code == 200
    stats = client.get("/v1/stats").json()
    assert stats["verified_reporters"] == 0, "a read must not create a reporter record"
    assert stats["observations_total"] == 0


def test_a_signed_recovery_outcome_is_accepted(client, identity):
    assert _signed_post(client, "/v1/observe", OBSERVATION, identity).status_code == 200
    fingerprint = client.post("/v1/query", json={"service": "api.github.com",
                                                 "operation": "create_issue",
                                                 "error_type": "rate_limit",
                                                 "error_code": "429"}).json()["fingerprint"]
    outcome = _signed_post(client, "/v1/outcome",
                           {"fingerprint": fingerprint, "action": "backoff", "successful": True},
                           identity)
    assert outcome.status_code == 200


# -- the claim that must not be overstated -----------------------------------


def test_the_documented_promise_stays_narrow():
    """Signing rules out impersonation. It does not make keys scarce, and
    nothing that ships may suggest it does."""
    from pathlib import Path

    from app.schemas.services import NetworkStats

    described = NetworkStats.model_fields["verified_reporters"].description
    assert "not Sybil resistance" in described
    source = Path("app/core/identity.py").read_text()
    assert "not Sybil resistance" in source
    assert "A thousand keys cost nothing" in source


def test_verification_is_independent_of_adoption(client, identity):
    """A verified reporter is not automatically an adopted one: five
    observations over ten minutes is what makes an agent count, and a key
    does not shortcut it."""
    _signed_post(client, "/v1/observe", OBSERVATION, identity)
    stats = client.get("/v1/stats").json()
    assert stats["verified_reporters"] == 1
    assert stats["real_observations_total"] == 0, "one signed row is still not adoption"


# -- the shipped client ------------------------------------------------------


def test_the_wrapper_signs_what_it_sends(monkeypatch, tmp_path):
    """Not "it produces headers" -- the headers it produces verify on the
    server, over the exact body it is about to post."""
    from failecho_autoreport import FailEcho

    monkeypatch.setenv("FAILECHO_IDENTITY_KEY", str(tmp_path / "id.key"))
    fe = FailEcho(endpoint="http://127.0.0.1:9", sign=True)
    assert fe.reporter_id.startswith("ed25519:"), "signing must make the key the identity"

    body = json.dumps(OBSERVATION).encode()
    headers = fe._headers("/v1/observe", body)
    assert server_identity.verify(
        headers["X-Reporter-ID"], headers["X-Reporter-Timestamp"],
        headers["X-Reporter-Signature"], "POST", "/v1/observe", body)
    assert not server_identity.verify(
        headers["X-Reporter-ID"], headers["X-Reporter-Timestamp"],
        headers["X-Reporter-Signature"], "POST", "/v1/observe", body + b" ")


def test_signing_is_off_unless_asked_for(monkeypatch, tmp_path):
    """It changes the reporter's identity, so it cannot happen to somebody."""
    from failecho_autoreport import FailEcho

    monkeypatch.setenv("FAILECHO_IDENTITY_KEY", str(tmp_path / "id.key"))
    monkeypatch.delenv("FAILECHO_SIGN", raising=False)
    fe = FailEcho(endpoint="http://127.0.0.1:9")
    assert fe.identity is None
    assert not fe.reporter_id.startswith("ed25519:")
    assert "X-Reporter-Signature" not in fe._headers("/v1/observe", b"{}")
    assert not (tmp_path / "id.key").exists(), "no key is written until asked for"


def test_the_cli_prints_the_id_and_the_limit(monkeypatch, tmp_path, capsys):
    from failecho_autoreport.__main__ import main

    monkeypatch.setenv("FAILECHO_IDENTITY_KEY", str(tmp_path / "id.key"))
    assert main(["identity"]) == 0
    printed = capsys.readouterr().out
    assert "ed25519:" in printed
    assert "not proof of a person" in printed
