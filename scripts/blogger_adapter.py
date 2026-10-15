#!/usr/bin/env python3
"""Convert an approved Jekyll post into a Blogger draft payload.

This module performs no network calls and never publishes a post. The returned
``is_draft`` flag is consumed by a later API client when it builds the request
query parameters.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin


_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_LIQUID_RELATIVE_URL_RE = re.compile(
    r"\{\{\s*['\"](?P<path>/[^'\"]+)['\"]\s*\|\s*relative_url\s*\}\}"
)


def _front_matter_and_body(markdown: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER_RE.match(markdown)
    if not match:
        raise ValueError("Jekyll front matter가 없거나 형식이 잘못되었습니다.")

    metadata: dict[str, str] = {}
    for line in match.group("front").splitlines():
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        metadata[key.strip()] = value
    return metadata, match.group("body").strip()


def _resolve_relative_urls(body: str, public_base_url: str) -> str:
    base = public_base_url.rstrip("/") + "/"

    def replace(match: re.Match[str]) -> str:
        return urljoin(base, match.group("path").lstrip("/"))

    return _LIQUID_RELATIVE_URL_RE.sub(replace, body)


def _remove_repeated_leading_title(body: str, title: str) -> str:
    """Remove the first post-body h1 when it repeats Blogger's post title."""
    title_pattern = re.escape(title.strip())
    pattern = re.compile(
        rf"\A\s*(?P<kicker><p\b[^>]*\bclass=[\"'][^\"']*\bpost-kicker\b[^\"']*[\"'][^>]*>.*?</p>\s*)?"
        rf"<h1\b[^>]*>\s*{title_pattern}\s*</h1>\s*",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(lambda match: match.group("kicker") or "", body, count=1)


def remove_language_switcher(content: str) -> str:
    """Pure cleanup for LIVE posts or copied Page bodies; preserve all other bytes.

    Match an exact HTML class token on real nav elements (not prose, comments,
    script text, or similar class names). Reject an unclosed managed nav rather
    than guessing where approved article content ends. No HTML serialization.
    """
    class NavSpans(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.lines = [0]
            self.lines.extend(match.end() for match in re.finditer("\n", content))
            self.depth = 0
            self.start: int | None = None
            self.spans: list[tuple[int, int]] = []

        def source_offset(self) -> int:
            line, column = self.getpos()
            return self.lines[line - 1] + column

        def handle_starttag(self, tag, attrs):
            if tag != "nav":
                return
            if self.start is not None:
                self.depth += 1
            elif any(name == "class" and "oneplate-language-switcher" in (value or "").split()
                     for name, value in attrs):
                self.start = self.source_offset()
                self.depth = 1

        def handle_endtag(self, tag):
            if tag == "nav" and self.start is not None:
                self.depth -= 1
                if self.depth == 0:
                    end = content.index(">", self.source_offset()) + 1
                    self.spans.append((self.start, end))
                    self.start = None

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    parser = NavSpans()
    parser.feed(content)
    parser.close()
    if parser.start is not None:
        raise ValueError("Unclosed managed oneplate-language-switcher nav; cleanup refused")
    for start, end in reversed(parser.spans):
        content = content[:start] + content[end:]
    return content


def inject_language_switcher(
    content: str,
    *,
    current_lang: str,
    ko_url: str,
    en_url: str,
) -> str:
    """Deprecated compatibility alias: remove managed visible links, never add.

    URL/language arguments remain accepted for older callers; identity metadata
    is independent of this display-only cleanup.
    """
    return remove_language_switcher(content)


def combined_label(series: str, lang: str) -> str:
    """Return the one public Blogger label that identifies series and language."""
    if not series or not re.fullmatch(r"[a-z0-9-]+", series):
        raise ValueError("안전한 series slug가 필요합니다.")
    if lang not in {"ko", "en"}:
        raise ValueError("lang은 ko 또는 en이어야 합니다.")
    return f"{series}-{lang}"


def label_identity(labels: object) -> tuple[str, str] | None:
    """Read the series/language identity from exactly one combined public label."""
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        return None
    identities: list[tuple[str, str]] = []
    for label in labels:
        match = re.fullmatch(r"([a-z0-9-]+)-(ko|en)", label)
        if match:
            identities.append((match.group(1), match.group(2)))
    if len(identities) != 1:
        return None
    return identities[0]


def build_blogger_payload(
    jekyll_post: str,
    *,
    public_base_url: str,
    draft: bool = True,
) -> dict[str, object]:
    """Return a network-free Blogger request plan for one Jekyll post."""
    metadata, body = _front_matter_and_body(jekyll_post)
    title = metadata.get("title", "").strip().strip('"\'')
    series = metadata.get("series", "").strip()
    lang = metadata.get("lang", "").strip()
    if not title:
        raise ValueError("front matter에 title이 필요합니다.")
    if not series or not re.fullmatch(r"[a-z0-9-]+", series):
        raise ValueError("front matter에 안전한 series slug가 필요합니다.")
    if lang not in {"ko", "en"}:
        raise ValueError("front matter의 lang은 ko 또는 en이어야 합니다.")

    content = remove_language_switcher(_resolve_relative_urls(body, public_base_url))
    content = _remove_repeated_leading_title(content, title)
    return {
        "title": title,
        "content": content,
        "labels": [combined_label(series, lang)],
        "is_draft": bool(draft),
        "source_permalink": metadata.get("permalink", ""),
    }


def blogger_request_body(payload: dict[str, object]) -> dict[str, object]:
    """Strip adapter-only fields before sending a Blogger API request."""
    return {
        key: payload[key]
        for key in ("title", "content", "labels")
        if key in payload
    }


if __name__ == "__main__":
    raise SystemExit("This module is a library; network publishing is intentionally not implemented.")
