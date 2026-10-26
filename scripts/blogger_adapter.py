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


_HTML_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
_HTML_RAW_TEXT_TAGS = frozenset({"script", "style", "textarea", "title"})
_HTML_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "details", "dialog", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "menu", "nav", "ol", "p", "pre",
    "section", "summary", "table", "ul",
})
_MALFORMED_TAG_TEXT_RE = re.compile(r"</?[a-z][a-z0-9:-]*(?:\s|/|>|$)|<!--|<![a-z]|<\?", re.IGNORECASE)


def _has_ambiguous_raw_angle_bracket(start_tag: str) -> bool:
    quote: str | None = None
    for char in start_tag[1:]:
        if quote:
            if char == quote:
                quote = None
            elif char in "<>":
                return True
        elif char in {'\"', "'"}:
            quote = char
        elif char == "<":
            return True
    return False


def _parse_html_tokens(
    content: str,
    *,
    reject_comments: bool = False,
    forbidden_tags: frozenset[str] = frozenset(),
    reject_ambiguous_angles: bool = False,
    reject_paragraphs_in_code: bool = False,
) -> tuple[tuple[object, ...], ...]:
    """Parse and validate a strictly balanced HTML fragment; fail closed on ambiguity."""
    from html import unescape

    class StrictHTMLParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.stack: list[str] = []
            self.tokens: list[tuple[object, ...]] = []
            self.issues: list[str] = []

        @staticmethod
        def normalized_attrs(attrs: list[tuple[str, str | None]]) -> tuple[tuple[str, str | None], ...]:
            return tuple(sorted(((name.lower(), value) for name, value in attrs), key=lambda item: (item[0], item[1] or "")))

        def append_data(self, data: str) -> None:
            if self.tokens and self.tokens[-1][0] == "data":
                previous = self.tokens[-1]
                self.tokens[-1] = ("data", str(previous[1]) + data)
            else:
                self.tokens.append(("data", data))

        def check_start(self, tag: str) -> None:
            if tag in forbidden_tags:
                self.issues.append(f"<{tag}> blocks are unsupported")
            if reject_ambiguous_angles and _has_ambiguous_raw_angle_bracket(self.get_starttag_text() or ""):
                self.issues.append("raw angle brackets inside start tags are unsupported")
            if reject_paragraphs_in_code and tag == "p" and "code" in self.stack:
                self.issues.append("paragraph markup inside code is unsupported")
            if "p" in self.stack and tag in _HTML_BLOCK_TAGS:
                self.issues.append(f"block element <{tag}> cannot be nested in a paragraph")

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.check_start(tag)
            self.tokens.append(("void" if tag in _HTML_VOID_TAGS else "start", tag.lower(), self.normalized_attrs(attrs)))
            if tag not in _HTML_VOID_TAGS:
                self.stack.append(tag.lower())

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.check_start(tag)
            normalized = tag.lower()
            if normalized not in _HTML_VOID_TAGS:
                self.issues.append(f"self-closing non-void <{tag}> is unsupported")
            kind = "void" if normalized in _HTML_VOID_TAGS else "startend"
            self.tokens.append((kind, normalized, self.normalized_attrs(attrs)))

        def handle_endtag(self, tag: str) -> None:
            normalized = tag.lower()
            self.tokens.append(("end", normalized))
            if normalized in _HTML_VOID_TAGS:
                self.issues.append(f"void element </{tag}> cannot be closed")
            elif not self.stack or self.stack[-1] != normalized:
                expected = self.stack[-1] if self.stack else "no open element"
                self.issues.append(f"mismatched </{tag}>; expected {expected}")
            else:
                self.stack.pop()

        def handle_data(self, data: str) -> None:
            if (not self.stack or self.stack[-1] not in _HTML_RAW_TEXT_TAGS) and _MALFORMED_TAG_TEXT_RE.search(data):
                self.issues.append("unparsed tag-like markup in text")
            self.append_data(data)

        def handle_entityref(self, name: str) -> None:
            self.append_data(unescape(f"&{name};"))

        def handle_charref(self, name: str) -> None:
            self.append_data(unescape(f"&#{name};"))

        def handle_comment(self, data: str) -> None:
            if reject_comments:
                self.issues.append("comments are unsupported")
            self.tokens.append(("comment", data))

        def handle_decl(self, decl: str) -> None:
            self.tokens.append(("decl", decl))

        def handle_pi(self, data: str) -> None:
            self.tokens.append(("pi", data))

    parser = StrictHTMLParser()
    parser.feed(content)
    if parser.rawdata:
        parser.issues.append("incomplete tag, comment, or entity at end of input")
    parser.close()
    if parser.stack:
        parser.issues.append("unclosed elements: " + ", ".join(parser.stack))
    if parser.issues:
        raise ValueError("Invalid or unsupported HTML structure: " + "; ".join(dict.fromkeys(parser.issues)))
    return tuple(parser.tokens)


def _validate_blogger_formatting_input(content: str) -> None:
    _parse_html_tokens(
        content,
        reject_comments=True,
        forbidden_tags=frozenset({"script", "style", "pre", "textarea", "title", "iframe", "object", "template"}),
        reject_ambiguous_angles=True,
        reject_paragraphs_in_code=True,
    )


def _visible_html_text(fragment: str) -> str:
    class TextParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    parser = TextParser()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.parts).replace("\xa0", " ").strip()


def format_blogger_publication_content(content: str) -> str:
    """Apply the owner's Blogger-only centered kicker, badge, and indent style."""
    _validate_blogger_formatting_input(content)
    leading = re.match(r"\A(?P<space>\s*)<p\b(?P<attrs>[^>]*)>(?P<body>.*?)</p>", content, re.IGNORECASE | re.DOTALL)
    if leading:
        class_match = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", leading.group("attrs"), re.IGNORECASE | re.DOTALL)
        if class_match and "post-kicker" in class_match.group(2).split():
            content = (
                leading.group("space")
                + '<h1 style="text-align: center;">'
                + leading.group("body")
                + "</h1>"
                + content[leading.end():]
            )

    badge_pattern = re.compile(
        r"(?P<open><a\b(?=[^>]*\bclass\s*=\s*([\"'])[^\"']*\bsource-badge\b[^\"']*\2)[^>]*>)"
        r"(?P<body>.*?)"
        r"(?P<close></a>)",
        re.IGNORECASE | re.DOTALL,
    )
    small_span_pattern = re.compile(
        r"\A\s*<span\b(?=[^>]*\bstyle\s*=\s*([\"'])[^\"']*font-size\s*:\s*xx-small[^\"']*\1)[^>]*>.*?</span>\s*\Z",
        re.IGNORECASE | re.DOTALL,
    )

    def wrap_badge(match: re.Match[str]) -> str:
        label = match.group("body")
        if small_span_pattern.fullmatch(label):
            return match.group(0)
        return match.group("open") + '<span style="font-size: xx-small;">' + label + "</span>" + match.group("close")

    content = badge_pattern.sub(wrap_badge, content)

    paragraph_pattern = re.compile(r"(?P<open><p\b(?P<attrs>[^>]*)>)(?P<body>.*?)(?P<close></p>)", re.IGNORECASE | re.DOTALL)
    excluded_classes = {"post-kicker", "post-meta"}

    def indent_paragraph(match: re.Match[str]) -> str:
        class_match = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", match.group("attrs"), re.IGNORECASE | re.DOTALL)
        classes = set(class_match.group(2).split()) if class_match else set()
        if classes & excluded_classes:
            return match.group(0)

        body = match.group("body")
        visible_body = _visible_html_text(body)
        if not visible_body:
            return match.group(0)
        is_summary_block = "summary" in classes
        leading_space = body[: len(body) - len(body.lstrip())]
        remaining = body[len(leading_space):]
        if not re.match(r"(?:&nbsp;|&#(?:160|x0*a0);)", remaining, re.IGNORECASE):
            remaining = "&nbsp;" + remaining
        body = leading_space + remaining

        breaks = [] if is_summary_block else list(re.finditer(r"<br\b[^>]*>", body, re.IGNORECASE))
        for br in reversed(breaks):
            tail = body[br.end():]
            boundary = re.search(r"<br\b|</p>", tail, re.IGNORECASE)
            segment = tail[:boundary.start()] if boundary else tail
            visible_segment = _visible_html_text(segment)
            if visible_segment and not re.match(r"\s*(?:&nbsp;|&#(?:160|x0*a0);)", segment, re.IGNORECASE):
                body = body[:br.end()] + "&nbsp;" + body[br.end():]
        return match.group("open") + body + match.group("close")

    return paragraph_pattern.sub(indent_paragraph, content)


def content_structure_signature(content: str) -> tuple[tuple[object, ...], ...]:
    """Validate and canonicalize an HTML fragment without flattening structure."""
    return _parse_html_tokens(content, reject_ambiguous_angles=True)



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
