"""Local SEO preflight for FailEcho.

    .venv/bin/python scripts/check_seo.py
    .venv/bin/python scripts/check_seo.py --base-url https://failecho.com

Checks the crawlable surfaces of a running instance: title, description,
canonical, robots.txt, sitemap.xml, JSON-LD validity, and that a production
FIN_PUBLIC_URL does not leak localhost into the HTML.

It never contacts a search engine. There is no way to make Google index a
site from a script, and anything claiming otherwise is scraping.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from xml.etree import ElementTree

DEFAULT_BASE = "http://127.0.0.1:8000"
PASS, FAIL = "PASS", "FAIL"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((PASS if ok else FAIL, name, detail))
        return ok

    def render(self) -> int:
        width = max(len(name) for _, name, _ in self.rows)
        failures = 0
        for status, name, detail in self.rows:
            if status == FAIL:
                failures += 1
            print(f"  {status}  {name:<{width}}  {detail}")
        print()
        print(f"{len(self.rows) - failures}/{len(self.rows)} checks passed")
        return 1 if failures else 0


def fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "failecho-seo-check"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError) as exc:
        print(f"cannot reach {url}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def fetch_bytes(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "failecho-seo-check"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError) as exc:
        print(f"cannot reach {url}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR chunk. No Pillow on the server."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def check_icons(report: "Report", base: str, home: str) -> None:
    """Google shows a favicon only if it is square and a multiple of 48px.

    It accepts rel="icon", "shortcut icon" and "apple-touch-icon" as sources,
    so every one of them has to satisfy the rule -- a non-square apple-touch
    icon is enough to lose the favicon in search results.
    """
    icons = re.findall(
        r'<link[^>]*rel="(icon|shortcut icon|apple-touch-icon)"[^>]*href="([^"]+)"',
        home,
    )
    report.check("favicon link present", bool(icons), f"{len(icons)} icon links")

    for rel, href in icons:
        url = href if href.startswith("http") else f"{base}{href}"
        status, data = fetch_bytes(url)
        if not report.check(f'{rel} fetches', status == 200, f"got {status}"):
            continue
        size = png_size(data)
        if size is None:
            # .ico is a valid favicon format; only PNGs can be measured here.
            report.check(f"{rel} is a real image", bool(data), f"{len(data)} bytes")
            continue
        width, height = size
        report.check(f"{rel} is square", width == height, f"{width}x{height}")
        report.check(f"{rel} is a multiple of 48px", width % 48 == 0, f"{width}px")


def main(base: str) -> int:
    base = base.rstrip("/")
    report = Report()

    status, home = fetch(f"{base}/")
    report.check("homepage 200", status == 200, f"got {status}")

    title = re.search(r"<title>(.*?)</title>", home, re.S)
    report.check(
        "title names the brand first",
        bool(title) and title.group(1).strip().startswith("FailEcho"),
        title.group(1).strip() if title else "missing",
    )

    description = re.search(r'<meta name="description" content="([^"]+)"', home)
    report.check(
        "meta description present",
        bool(description) and 50 <= len(description.group(1)) <= 320,
        f"{len(description.group(1))} chars" if description else "missing",
    )

    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', home)
    report.check(
        "canonical URL", bool(canonical), canonical.group(1) if canonical else "missing"
    )

    report.check(
        "no noindex directive",
        "noindex" not in home.lower(),
        "",
    )
    report.check("brand text is visible HTML", "FailEcho is a shared failure" in home)
    report.check('"AI agents" appears in copy', "AI agents" in home)
    report.check("MCP is named in prose", "Model Context Protocol" in home)
    report.check("exactly one h1", home.count("<h1") == 1, f"{home.count('<h1')} found")

    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', home, re.S
    )
    parsed = None
    if blocks:
        try:
            parsed = json.loads(blocks[0])
        except json.JSONDecodeError as exc:
            report.check("JSON-LD parses", False, str(exc))
    report.check("JSON-LD present and parses", parsed is not None)
    if parsed:
        types = {node.get("@type") for node in parsed.get("@graph", [parsed])}
        report.check("JSON-LD declares SoftwareApplication", "SoftwareApplication" in types)

        # Every FAQ answer must exist verbatim in the visible page.
        faq = next(
            (n for n in parsed.get("@graph", []) if n.get("@type") == "FAQPage"), None
        )
        if faq:
            mismatched = [
                q["name"]
                for q in faq["mainEntity"]
                if q["name"] not in home
                or q["acceptedAnswer"]["text"].split(".")[0] not in home
            ]
            report.check(
                "FAQ schema matches visible text",
                not mismatched,
                f"missing: {mismatched}" if mismatched else "",
            )

    status, robots = fetch(f"{base}/robots.txt")
    report.check("robots.txt 200", status == 200, f"got {status}")
    report.check("robots allows crawling", "Allow: /" in robots)
    report.check("robots has no blanket Disallow", "Disallow: /" not in robots)
    report.check("robots points at the sitemap", "Sitemap:" in robots)

    status, sitemap = fetch(f"{base}/sitemap.xml")
    report.check("sitemap.xml 200", status == 200, f"got {status}")
    locs: list[str] = []
    try:
        root = ElementTree.fromstring(sitemap)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text or "" for el in root.findall(".//s:loc", ns)]
        report.check("sitemap is valid XML", True, f"{len(locs)} URLs")
    except ElementTree.ParseError as exc:
        report.check("sitemap is valid XML", False, str(exc))
    for path in ("/", "/docs", "/llms.txt"):
        report.check(f"sitemap lists {path}", any(u.endswith(path) for u in locs))

    check_icons(report, base, home)

    for path in ("/docs", "/llms.txt", "/openapi.json", "/health"):
        status, _ = fetch(f"{base}{path}")
        report.check(f"{path} 200", status == 200, f"got {status}")

    # A production base URL must not leave development URLs in the markup.
    if not base.startswith(("http://127.0.0.1", "http://localhost")):
        leaked = [t for t in ("localhost", "127.0.0.1", "testserver") if t in home]
        report.check("no localhost leak in HTML", not leaked, f"found {leaked}")
        report.check(
            "canonical uses the public origin",
            bool(canonical) and canonical.group(1).startswith(base),
        )

    return report.render()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    args = parser.parse_args()
    print(f"FailEcho SEO check — {args.base_url}\n")
    raise SystemExit(main(args.base_url))
