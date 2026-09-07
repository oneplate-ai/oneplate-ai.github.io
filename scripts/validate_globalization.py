#!/usr/bin/env python3
"""Validate the generated bilingual AI One Plate site."""
from __future__ import annotations

import argparse
import html.parser
from pathlib import Path
import urllib.parse
import xml.etree.ElementTree as ET


class PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lang = None
        self.links: list[dict[str, str]] = []
        self.meta: list[dict[str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        if tag == "html":
            self.lang = data.get("lang")
        if tag == "link" and data.get("rel") in {"canonical", "alternate"}:
            self.links.append(data)
        if tag == "meta" and (data.get("property", "").startswith("og:") or data.get("name", "").startswith("twitter:")):
            self.meta.append(data)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def local_target(root: Path, source: Path, value: str) -> Path | None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme or parsed.netloc or value.startswith(("#", "mailto:", "tel:", "data:")):
        return None
    path = urllib.parse.unquote(parsed.path)
    if not path:
        return None
    return (root / path.lstrip("/")) if path.startswith("/") else (source.parent / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    root = args.site.resolve()
    errors: list[str] = []
    pages = sorted(root.rglob("*.html"))
    if not pages:
        errors.append("generated site has no HTML pages")

    lang_counts: dict[str, int] = {}
    parsed_pages: dict[str, PageParser] = {}
    for path in pages:
        parsed = PageParser()
        parsed.feed(path.read_text(encoding="utf-8"))
        parsed_pages[str(path.relative_to(root))] = parsed
        lang_counts[parsed.lang or "missing"] = lang_counts.get(parsed.lang or "missing", 0) + 1
        if parsed.lang not in {"ko-KR", "en"}:
            errors.append(f"{path}: unsupported html lang {parsed.lang!r}")
        content = path.read_text(encoding="utf-8")
        for marker in ('class="language-switcher"', 'id="language-menu"', "🇰🇷", "🇺🇸"):
            if marker not in content:
                errors.append(f"{path}: missing {marker}")
        canonicals = [link.get("href") for link in parsed.links if link.get("rel") == "canonical"]
        if len(canonicals) != 1:
            errors.append(f"{path}: expected one canonical, found {len(canonicals)}")
        for link in parsed.links:
            target = local_target(root, path, link.get("href", ""))
            if target is not None and not target.exists():
                errors.append(f"{path}: missing local {link.get('href')}")
        for attr in ("og:title", "og:description", "og:url"):
            if not any(meta.get("property") == attr for meta in parsed.meta):
                errors.append(f"{path}: missing {attr}")

    expected_pairs = [
        ("index.html", "en/index.html", "https://oneplate-ai.github.io/", "https://oneplate-ai.github.io/en/"),
        ("posts/2026-09-03-easy-ai-01.html", "en/posts/2026-09-03-easy-ai-01.html", "https://oneplate-ai.github.io/posts/2026-09-03-easy-ai-01.html", "https://oneplate-ai.github.io/en/posts/2026-09-03-easy-ai-01.html"),
        ("posts/2026-09-04-easy-ai-02.html", "en/posts/2026-09-04-easy-ai-02.html", "https://oneplate-ai.github.io/posts/2026-09-04-easy-ai-02.html", "https://oneplate-ai.github.io/en/posts/2026-09-04-easy-ai-02.html"),
    ]
    for ko, en, ko_url, en_url in expected_pairs:
        for rel, hreflang, expected in ((ko, "en", en_url), (en, "ko-KR", ko_url)):
            parsed = parsed_pages.get(rel)
            if parsed is None:
                errors.append(f"missing expected page {rel}")
                continue
            values = [link.get("href") for link in parsed.links if link.get("rel") == "alternate" and link.get("hreflang") == hreflang]
            if values != [expected]:
                errors.append(f"{rel}: hreflang {hreflang}={values}, expected {expected}")

    sitemap = root / "sitemap.xml"
    if not sitemap.exists():
        errors.append("sitemap.xml missing")
    else:
        try:
            sitemap_root = ET.fromstring(sitemap.read_text(encoding="utf-8"))
            sitemap_urls = [element.text for element in sitemap_root.findall(".//{*}loc")]
            for url in sitemap_urls:
                parsed_url = urllib.parse.urlparse(url or "")
                target = local_target(root, root / "sitemap.xml", parsed_url.path)
                if target is not None and not target.exists():
                    errors.append(f"sitemap target missing: {url}")
            required = {
                "https://oneplate-ai.github.io/",
                "https://oneplate-ai.github.io/en/",
                "https://oneplate-ai.github.io/en/series/easy-ai/",
                "https://oneplate-ai.github.io/en/posts/2026-09-03-easy-ai-01.html",
                "https://oneplate-ai.github.io/en/posts/2026-09-04-easy-ai-02.html",
            }
            missing = sorted(required - set(sitemap_urls))
            errors.extend(f"sitemap required URL missing: {url}" for url in missing)
        except ET.ParseError as exc:
            errors.append(f"sitemap.xml is invalid XML: {exc}")

    print(f"pages={len(pages)} languages={lang_counts} errors={len(errors)}")
    if errors:
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("GLOBALIZATION_VALIDATION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
