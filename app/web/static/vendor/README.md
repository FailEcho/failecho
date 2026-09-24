# Vendored: Swagger UI

`swagger-ui-bundle.js` and `swagger-ui.css` are **unmodified upstream build
output** from [`swagger-ui-dist`](https://www.npmjs.com/package/swagger-ui-dist)
**5.33.0** (Apache-2.0; `SWAGGER-UI-LICENSE`, `SWAGGER-UI-NOTICE`). They are
minified because that is how upstream ships them; they are not FailEcho code.

They are served from this origin rather than a CDN on purpose (penetration test,
14 Sep 2026): `/docs` then runs no third-party script, and the site's
`Content-Security-Policy: script-src 'self'` holds. `swagger-init.js` is ours:
FastAPI's inline bootstrap, moved to a file so that policy can stay strict.

Verify them yourself:

    npm pack swagger-ui-dist@5.33.0 && tar -xzf swagger-ui-dist-5.33.0.tgz
    sha256sum package/swagger-ui-bundle.js package/swagger-ui.css

| File | sha256 |
|---|---|
| `swagger-ui-bundle.js` | `62df541529080464a7660adc793eab7128c6193ce3be24ddc1e0e0a4a63edc2f` |
| `swagger-ui.css` | `1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321` |

`tests/test_vendor.py` fails if either file stops matching its checksum, so
an edit to vendored code cannot land unnoticed.
