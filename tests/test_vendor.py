"""Vendored third-party files are exactly what upstream published.

A trust scanner flagged the minified Swagger UI bundle as code nobody can
audit (24 Sep). It is upstream's own build output, unmodified; this pins it
to the checksums of swagger-ui-dist 5.33.0 on npm, so an edit -- or a swap
for something else -- fails here rather than shipping."""

import hashlib
import pathlib
import re

VENDOR = pathlib.Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "vendor"


def test_vendored_swagger_matches_its_recorded_upstream_checksums():
    readme = (VENDOR / "README.md").read_text()
    recorded = dict(re.findall(r"\| `([\w.-]+)` \| `([0-9a-f]{64})` \|", readme))
    assert set(recorded) == {"swagger-ui-bundle.js", "swagger-ui.css"}
    for name, digest in recorded.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_the_licence_travels_with_the_code():
    assert "Apache License" in (VENDOR / "SWAGGER-UI-LICENSE").read_text()
