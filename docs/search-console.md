# Getting FailEcho indexed

These steps are **manual**. No script, and no part of this application, can make
Google index a site — submission and verification happen in Google's own
console, and indexing happens on Google's schedule. Expect days, not minutes,
for a new domain.

Before starting, confirm the site is ready:

```bash
.venv/bin/python scripts/check_seo.py --base-url https://failecho.com
```

All checks should pass. If any fail, fix them first — submitting a site Google
cannot crawl properly just wastes the first crawl.

---

## Why this is needed

`failecho.com` is a new domain with no inbound links. Google currently reads
"FailEcho" as the generic phrase *fail echo* (a shell command), because nothing
yet tells it that FailEcho is a product name. The fix is entity clarity plus
time: a title that leads with the brand, visible text stating what FailEcho is,
structured data, a sitemap, and eventually links from places Google already
trusts.

---

## Google Search Console

1. Open <https://search.google.com/search-console>.
2. **Add property.**
3. Choose **Domain** property (`failecho.com`) rather than URL prefix — DNS
   verification is available because Cloudflare hosts the zone, and a domain
   property covers `www`, `http`, `https` and every subdomain in one go.
4. Google shows a **TXT record**. In Cloudflare → `failecho.com` → **DNS →
   Records → Add record**:
   - Type: `TXT`
   - Name: `@`
   - Content: the `google-site-verification=…` string Google gave you
   - Proxy status: not applicable to TXT
5. Back in Search Console, click **Verify**. DNS usually propagates in under a
   minute on Cloudflare.
6. Open **URL Inspection** (top search bar).
7. Inspect `https://failecho.com/`. It will say "URL is not on Google" for a
   new site — that is expected.
8. Click **Request Indexing**. This queues one crawl; it does not guarantee or
   accelerate ranking.
9. Inspect `https://failecho.com/docs` and request indexing for it too.
10. Open **Sitemaps** in the left nav.
11. Submit: `https://failecho.com/sitemap.xml`
12. Wait for the first crawl, then open **Page Indexing** and check for:
    - **Blocked by robots.txt** — should not happen; `robots.txt` allows all
    - **Excluded by 'noindex'** — there is no `noindex` anywhere in this project
    - **Duplicate, Google chose a different canonical** — would indicate the
      `.dev` domain is serving content instead of redirecting
    - **Page with redirect** — expected for `www.failecho.com`, correct behaviour
    - **Crawl errors / 5xx** — check `failecho status` and the Caddy log

Once indexed, search `site:failecho.com` to see exactly what Google holds.

---

## Bing Webmaster Tools

Worth ten minutes, not more. Bing also feeds DuckDuckGo.

1. Open <https://www.bing.com/webmasters>.
2. Add `https://failecho.com`. You can **import from Google Search Console**,
   which carries verification across and saves the DNS step.
3. Submit the same sitemap: `https://failecho.com/sitemap.xml`.
4. Use **URL Submission** for the homepage.

---

## What actually moves brand search

Search Console gets you crawled. It does not make Google decide that "FailEcho"
is a proper noun. What does that is **other trusted sites using the name and
linking to the domain**:

- the public GitHub repository, with `failecho.com` in the About field
- MCP server directories and registries listing FailEcho with its endpoint
- a Show HN or similar post that people link to
- package metadata, if the client is ever published

Each of these is a place Google already crawls, using the word FailEcho next to
the URL. That association is what turns a generic phrase into an entity. It
takes weeks, and there is no shortcut worth taking — buying links or spinning up
filler pages is how a new domain gets classified as spam instead.

---

## What we already did on the site

For reference when diagnosing an indexing problem:

| Signal | Where |
|---|---|
| Brand-first `<title>` | `FailEcho — Failure Intelligence for AI Agents` |
| Meta description | states the category in one sentence |
| Single `<h1>` | the FailEcho wordmark, `alt="FailEcho"` |
| Visible entity sentence | "FailEcho is a shared failure intelligence network for AI agents and autonomous software." |
| Canonical | `https://failecho.com/`, from `FIN_PUBLIC_URL` |
| Structured data | `SoftwareApplication`, `WebSite`, `FAQPage` (JSON-LD) |
| `robots.txt` | allows everything, names the sitemap |
| `sitemap.xml` | `/`, `/docs`, `/llms.txt` with real `lastmod` |
| `/docs` | canonical + description injected into Swagger UI |
| `/llms.txt` | brand, category and canonical site for AI crawlers |
| Redirects | `www` → apex, `.dev` → `.com`, both 301 with paths preserved |
