#!/usr/bin/env python3
"""Synchronize a series landing page and every standalone post archive."""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(?P<series>.+)-\d+\.html$")
TITLE_RE = re.compile(r'<h1>(.*?)</h1>', re.S)
KICKER_RE = re.compile(r'<p class="post-kicker">(.*?)</p>', re.S)
ARTICLE_RE = re.compile(r'(<article)(?P<attrs>[^>]*)>(?P<body>.*?)</article>', re.S)
ARCHIVE_RE = re.compile(r'<aside class="series-archive".*?</aside>', re.S)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def discover_posts(root: Path, series: str) -> list[tuple[str, Path, str, str]]:
    found = []
    for path in (root / "posts").glob("*.html"):
        match = POST_RE.match(path.name)
        if not match or match.group("series") != series:
            continue
        text = path.read_text(encoding="utf-8")
        title = TITLE_RE.search(text)
        kicker = KICKER_RE.search(text)
        if not title or not kicker:
            raise ValueError(f"missing title or kicker: {path}")
        found.append((match.group(1), path, clean(title.group(1)), clean(kicker.group(1))))
    if not found:
        raise ValueError(f"no public posts found for series: {series}")
    return sorted(found, reverse=True, key=lambda item: (item[0], item[1].name))


def archive_html(posts, current: Path, prefix: str, current_href: str = "#content") -> str:
    items = []
    for date, path, title, _kicker in posts:
        is_current = path == current
        href = current_href if is_current else f"{prefix}{path.name}"
        status = "현재 읽고 있는 글" if is_current else "지난 글"
        class_attr = ' class="current"' if is_current else ""
        items.append(
            f'        <li><a{class_attr} href="{href}"><time datetime="{date}">'
            f'{date.replace("-", ". ")}</time><strong>{html.escape(title)}</strong>'
            f'<span>{status}</span></a></li>'
        )
    return (
        '      <aside class="series-archive" aria-labelledby="archive-title">\n'
        '        <h2 id="archive-title">쉽게 먹는 AI 목록</h2>\n'
        '        <ol class="series-archive-list">\n'
        + "\n".join(items)
        + '\n        </ol>\n      </aside>'
    )


def replace_archive(text: str, replacement: str) -> str:
    if not ARCHIVE_RE.search(text):
        raise ValueError("series archive block not found")
    return ARCHIVE_RE.sub(replacement, text, count=1)


def update(root: Path, series: str) -> None:
    posts = discover_posts(root, series)
    for _date, path, _title, _kicker in posts:
        text = path.read_text(encoding="utf-8")
        replacement = archive_html(posts, path, "")
        path.write_text(replace_archive(text, replacement), encoding="utf-8")

    landing = root / "series" / series / "index.html"
    landing_text = landing.read_text(encoding="utf-8")
    newest = posts[0][1].read_text(encoding="utf-8")
    newest_article = ARTICLE_RE.search(newest)
    landing_article = re.search(r'<article id="latest".*?</article>', landing_text, re.S)
    if not newest_article or not landing_article:
        raise ValueError("article block not found in post or landing page")
    article_body = newest_article.group("body")
    landing_text = landing_text[:landing_article.start()] + '<article id="latest">' + article_body + '</article>' + landing_text[landing_article.end():]
    landing_archive = archive_html(posts, posts[0][1], "../../posts/", "#latest")
    landing_text = replace_archive(landing_text, landing_archive)
    landing.write_text(landing_text, encoding="utf-8")
    print(f"updated {len(posts)} posts and {landing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", help="series slug, matching the post filename segment")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    update(args.root, args.series)


if __name__ == "__main__":
    main()
