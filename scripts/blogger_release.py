#!/usr/bin/env python3
"""Synchronize one approved bilingual release unit directly from drafts to Blogger.

Default mode is read-only planning.  --apply is idempotent and does not require
GitHub Pages public posts, URLs, builds, or deployment state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from blogger_adapter import build_blogger_payload, inject_language_switcher
from blogger_full_parity_sync import fresh_access_token, strip_navigation
from blogger_series_navigation import NAV_PATTERN
from blogger_publish import BloggerApiError, BloggerClient, build_client_payload
from publication_gate import GateError, validate_approval


DEFAULT_BLOG_ID = "4141805834166693370"
DEFAULT_TOKEN_PATH = Path("/root/.hermes/credentials/blogger-token.json")
DEFAULT_CLIENT_SECRET_PATH = Path("/root/.hermes/credentials/blogger-client-secret.json")


class BloggerReleaseError(RuntimeError):
    """The approved Blogger release cannot be planned or verified safely."""


_LANGUAGE_SWITCHER_RE = re.compile(
    r"\s*<nav\b[^>]*class=[\"'][^\"']*\boneplate-language-switcher\b[^\"']*[\"'][^>]*>.*?</nav>\s*",
    re.IGNORECASE | re.DOTALL,
)
_DRAFT_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_VERIFICATION_NOTE_RE = re.compile(
    r"(?P<open><(?:p|aside)\b[^>]*\bclass=[\"'][^\"']*\bpost-verification\b[^\"']*[\"'][^>]*>)"
    r"(?P<body>.*?)"
    r"(?P<close></(?:p|aside)>)",
    re.IGNORECASE | re.DOTALL,
)
_ARTICLE_IMAGE_URL_RE = re.compile(
    r"(?P<url>(?:(?P<scheme>https?):)?//(?P<userinfo>[^/?#\s@]+@)?"
    r"(?P<host>[^/:?#\s]+)(?::(?P<port>\d+))?"
    r"(?P<path>/[^\"'\s,)>]+))",
    re.IGNORECASE,
)


def strip_language_switcher(content: str) -> str:
    """Remove only the generated counterpart switcher from a Blogger body."""
    return _LANGUAGE_SWITCHER_RE.sub("\n", content).strip()


def _fetch_public_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "AI-One-Plate release verifier"})
    with urlopen(request, timeout=30) as response:
        if response.status < 200 or response.status >= 300:
            raise BloggerReleaseError(f"공개 URL HTTP 상태가 성공이 아닙니다: {url} ({response.status})")
        return response.read().decode("utf-8", errors="replace")


def verify_pages_public_pair(
    approval: dict[str, Any],
    pages_base_url: str,
    *,
    fetch: Callable[[str], str] = _fetch_public_html,
) -> list[str]:
    """Require both approved Pages URLs before a Blogger promotion may run."""
    checked: list[str] = []
    for item in approval["drafts"]:
        url = urljoin(pages_base_url.rstrip("/") + "/", str(item["permalink"]).lstrip("/"))
        content = fetch(url)
        title = str(item["title"])
        if title not in content:
            raise BloggerReleaseError(f"Pages 공개 URL에서 승인 제목을 찾지 못했습니다: {url}")
        checked.append(url)
    return checked


def optional_pages_readback(
    approval: dict[str, Any],
    pages_base_url: str,
    *,
    required: bool,
    fetch: Callable[[str], str] = _fetch_public_html,
) -> list[str]:
    """Use Pages verification only when the transitional policy explicitly requires it."""
    if not required:
        return []
    return verify_pages_public_pair(approval, pages_base_url, fetch=fetch)


def verify_blogger_public_pair(
    sources: dict[tuple[str, str], dict[str, Any]],
    current: dict[tuple[str, str], tuple[str, dict[str, Any]]],
    *,
    fetch: Callable[[str], str] = _fetch_public_html,
) -> list[str]:
    """Require both live Blogger URLs to expose their approved titles."""
    checked: list[str] = []
    for key, source in sources.items():
        actual = current.get(key)
        if actual is None or actual[0] != "LIVE":
            raise BloggerReleaseError(f"Blogger 공개 URL 검증 대상이 LIVE가 아닙니다: {key}")
        url = actual[1].get("url")
        if not isinstance(url, str) or not url:
            raise BloggerReleaseError(f"Blogger 공개 URL이 없습니다: {key}")
        if str(source["title"]) not in fetch(url):
            raise BloggerReleaseError(f"Blogger 공개 URL에서 승인 제목을 찾지 못했습니다: {url}")
        checked.append(url)
    return checked


def require_immediate_publish_time(publish_date: str, *, now: datetime | None = None) -> None:
    """Reject future Blogger scheduling; this release unit verifies LIVE now."""
    try:
        scheduled = datetime.fromisoformat(publish_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise BloggerReleaseError(f"scheduled_publish_at 형식이 올바르지 않습니다: {publish_date}") from error
    if scheduled.tzinfo is None:
        raise BloggerReleaseError("scheduled_publish_at에는 시간대가 필요합니다.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise BloggerReleaseError("현재 시각에는 시간대가 필요합니다.")
    if scheduled > current:
        raise BloggerReleaseError(
            "미래 Blogger 예약 발행은 이 LIVE 검증 release에서 지원하지 않습니다. "
            "예약 시각 이후에 동일 승인 release unit을 실행하십시오."
        )


def public_copy_from_draft(text: str) -> str:
    """Apply only the approved front-matter and marked verification-note changes."""
    match = _DRAFT_FRONT_MATTER_RE.match(text)
    if not match:
        raise BloggerReleaseError("승인 초안 front matter가 없거나 형식이 올바르지 않습니다.")
    front_lines: list[str] = []
    for line in match.group("front").splitlines():
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in {"draft", "noindex"}:
            continue
        if key == "published" and re.fullmatch(r"published:\s*false\s*", line):
            front_lines.append("published: true")
        else:
            front_lines.append(line)

    def replace_verification_note(note: re.Match[str]) -> str:
        body = note.group("body").replace("이 초안은", "이번 게시글은").replace("This draft", "This post")
        return note.group("open") + body + note.group("close")

    body = _VERIFICATION_NOTE_RE.sub(replace_verification_note, match.group("body"))
    return "---\n" + "\n".join(front_lines) + "\n---\n" + body


def _language(payload: dict[str, object], path: Path) -> str:
    labels = payload.get("labels", [])
    if not isinstance(labels, list):
        raise BloggerReleaseError(f"Blogger label을 읽을 수 없습니다: {path}")
    languages = [value for value in labels if value in {"ko", "en"}]
    if len(languages) != 1:
        raise BloggerReleaseError(f"Blogger 언어 label은 ko 또는 en 하나여야 합니다: {path}")
    return languages[0]


def _image_urls(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        raise BloggerReleaseError("Blogger 이미지 URL mapping 형식이 올바르지 않습니다.")
    for name, value in data.items():
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise BloggerReleaseError(f"Blogger 이미지 mapping 키가 올바르지 않습니다: {name!r}")
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host == "oneplate-ai.github.io":
            raise BloggerReleaseError(f"Blogger 이미지 URL은 Pages가 아닌 HTTPS URL이어야 합니다: {value}")
        if not (host.endswith(".googleusercontent.com") or host.endswith(".bp.blogspot.com")):
            raise BloggerReleaseError(f"Blogger 이미지 URL host가 허용되지 않습니다: {value}")
    return {f"https://oneplate-ai.github.io/assets/images/{name}": url for name, url in data.items()}


def pages_image_sources(content: str) -> dict[str, str]:
    """Return every Pages image spelling mapped to its canonical map key."""
    sources: dict[str, str] = {}
    for match in _ARTICLE_IMAGE_URL_RE.finditer(content):
        host = match.group("host").rstrip(".").lower()
        if host != "oneplate-ai.github.io":
            continue
        if match.group("userinfo"):
            raise BloggerReleaseError("Pages 이미지 URL에 userinfo가 포함될 수 없습니다.")
        sources[match.group("url")] = "https://oneplate-ai.github.io" + match.group("path")
    return sources


def apply_image_mapping(content: str, image_urls: dict[str, str]) -> str:
    """Replace every normalized Pages image spelling with approved Blogger media."""
    def replace(match: re.Match[str]) -> str:
        host = match.group("host").rstrip(".").lower()
        if host != "oneplate-ai.github.io":
            return match.group("url")
        if match.group("userinfo"):
            raise BloggerReleaseError("Pages 이미지 URL에 userinfo가 포함될 수 없습니다.")
        return image_urls["https://oneplate-ai.github.io" + match.group("path")]
    return _ARTICLE_IMAGE_URL_RE.sub(replace, content)


def ensure_image_mapping(content: str, image_urls: dict[str, str]) -> None:
    """Fail closed when any Pages-hosted article image lacks a Blogger URL."""
    sources = pages_image_sources(content)
    missing = sorted(set(sources.values()) - set(image_urls))
    if missing:
        raise BloggerReleaseError("Blogger 이미지 URL mapping이 없습니다: " + ", ".join(missing))


def verify_image_map_approval(approval: dict[str, Any], image_url_path: Path) -> None:
    """Require the exact image map to be cryptographically bound to this release."""
    expected = approval.get("blogger_image_map_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BloggerReleaseError("Blogger release 승인 기록에 blogger_image_map_sha256가 필요합니다.")
    actual = hashlib.sha256(image_url_path.read_bytes()).hexdigest()
    if actual != expected:
        raise BloggerReleaseError("Blogger 이미지 URL mapping SHA-256이 승인 기록과 일치하지 않습니다.")


def release_sources(
    root: Path,
    approval_path: Path,
    image_url_path: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build Blogger release payloads directly from the exact approved drafts."""
    try:
        approval = validate_approval(approval_path.resolve(), root.resolve())
    except (GateError, OSError, ValueError) as error:
        raise BloggerReleaseError(f"승인 원본 검증을 통과하지 못했습니다: {error}") from error

    publish_date = str(approval["scheduled_publish_at"])
    verify_image_map_approval(approval, image_url_path)
    image_urls = _image_urls(image_url_path)
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    for draft_item in approval["drafts"]:
        draft_path = (root / str(draft_item["exact_draft_path"])).resolve()
        draft_text = draft_path.read_text(encoding="utf-8")
        public_text = public_copy_from_draft(draft_text)
        try:
            payload = build_blogger_payload(
                public_text,
                # Canonicalize draft-local asset paths into the legacy source
                # namespace only so the mandatory Blogger image map can replace
                # them before any API write. This does not fetch or publish Pages.
                public_base_url="https://oneplate-ai.github.io",
                draft=False,
            )
        except ValueError as error:
            raise BloggerReleaseError(f"승인 초안을 Blogger payload로 변환하지 못했습니다: {draft_item['exact_draft_path']}: {error}") from error
        language = _language(payload, draft_path)
        if language != draft_item["lang"] or payload.get("title") != draft_item["title"]:
            raise BloggerReleaseError(f"승인 기록과 초안 payload가 일치하지 않습니다: {draft_item['exact_draft_path']}")
        ensure_image_mapping(str(payload["content"]), image_urls)
        content = apply_image_mapping(str(payload["content"]), image_urls)
        if pages_image_sources(content):
            raise BloggerReleaseError(f"Blogger payload에 Pages 이미지 URL이 남았습니다: {draft_item['exact_draft_path']}")
        key = (str(payload["title"]), language)
        if key in sources:
            raise BloggerReleaseError(f"Blogger title/language key가 중복됩니다: {key}")
        labels = payload.get("labels", [])
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise BloggerReleaseError(f"Blogger label 형식이 올바르지 않습니다: {draft_path}")
        sources[key] = {
            "key": key,
            "title": str(payload["title"]),
            "language": language,
            "labels": sorted(labels),
            "content": content,
            "publish_date": publish_date,
            "source_path": str(draft_item["exact_draft_path"]),
        }
    if set(item["language"] for item in sources.values()) != {"ko", "en"}:
        raise BloggerReleaseError("Blogger release는 승인된 한국어·영어 초안 쌍이 필요합니다.")
    return sources


def index_posts(posts: list[dict[str, Any]], state: str) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    indexed: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for post in posts:
        labels = post.get("labels", [])
        languages = [label for label in labels if label in {"ko", "en"}]
        if len(languages) != 1:
            raise BloggerReleaseError(f"Blogger 글의 언어 label이 올바르지 않습니다: {post.get('id')}")
        key = (str(post.get("title", "")), languages[0])
        if key in indexed:
            raise BloggerReleaseError(f"Blogger 글 title/language가 중복됩니다: {key}")
        indexed[key] = (state, post)
    return indexed


def plan_release(
    sources: dict[tuple[str, str], dict[str, Any]],
    current: dict[tuple[str, str], tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return only required idempotent actions for the two approved records."""
    changes: list[dict[str, Any]] = []
    for key, source in sorted(sources.items()):
        existing = current.get(key)
        if existing is None:
            changes.append({"action": "create_draft_then_publish", "key": key})
            continue
        state, post = existing
        source_series = set(source["labels"]) - {"ko", "en"}
        existing_series = set(post.get("labels", [])) - {"ko", "en"}
        if source_series != existing_series:
            raise BloggerReleaseError(
                f"Blogger title/language 충돌: 다른 series 글을 덮어쓸 수 없습니다: {key} "
                f"(expected={sorted(source_series)}, actual={sorted(existing_series)})"
            )
        labels_match = sorted(post.get("labels", [])) == source["labels"]
        actual_content = strip_navigation(str(post.get("content", ""))) if state == "LIVE" else str(post.get("content", ""))
        actual_content = strip_language_switcher(actual_content)
        content_match = actual_content.strip() == str(source["content"]).strip()
        if state == "LIVE":
            if not labels_match or not content_match:
                navigation = NAV_PATTERN.search(str(post.get("content", "")))
                changes.append({
                    "action": "update_live",
                    "id": str(post["id"]),
                    "key": key,
                    "navigation": navigation.group(0) if navigation else "",
                })
        elif state == "DRAFT":
            action = "publish_draft" if labels_match and content_match else "update_draft_then_publish"
            changes.append({"action": action, "id": str(post["id"]), "key": key})
        else:
            raise BloggerReleaseError(f"지원하지 않는 Blogger state: {state}")

    if {str(source["language"]) for source in sources.values()} == {"ko", "en"}:
        live_keys = {key for key, actual in current.items() if key in sources and actual[0] == "LIVE"}
        if live_keys == set(sources):
            switcher_keys = {
                next(source["key"] for source in sources.values() if source["language"] == update["language"])
                for update in language_switcher_updates(sources, current)
            }
            switcher_keys.update(tuple(change["key"]) for change in changes if change["action"] == "update_live")
            for key in sorted(switcher_keys):
                post_id = str(current[key][1]["id"])
                changes.append({"action": "update_language_switcher", "id": post_id, "key": key})
        else:
            changes.append({"action": "update_language_switchers_after_publish", "keys": sorted(sources)})
    return changes


def language_switcher_updates(
    sources: dict[tuple[str, str], dict[str, Any]],
    current: dict[tuple[str, str], tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build only needed reciprocal-language switcher updates for a live pair."""
    by_language = {str(source["language"]): source for source in sources.values()}
    if set(by_language) != {"ko", "en"}:
        raise BloggerReleaseError("언어 전환 링크에는 한·영 Blogger release 쌍이 필요합니다.")
    posts: dict[str, dict[str, Any]] = {}
    for language, source in by_language.items():
        actual = current.get(tuple(source["key"]))
        if actual is None or actual[0] != "LIVE":
            raise BloggerReleaseError(f"언어 전환 링크 대상이 LIVE가 아닙니다: {language}")
        post = actual[1]
        if not isinstance(post.get("url"), str) or not post["url"]:
            raise BloggerReleaseError(f"언어 전환 링크 대상 URL이 없습니다: {language}")
        posts[language] = post

    updates: list[dict[str, Any]] = []
    for language in ("ko", "en"):
        post = posts[language]
        expected = inject_language_switcher(
            str(post.get("content", "")),
            current_lang=language,
            ko_url=str(posts["ko"]["url"]),
            en_url=str(posts["en"]["url"]),
        )
        if expected != post.get("content"):
            updates.append({"id": str(post["id"]), "content": expected, "language": language})
    return updates


def verify_language_switchers(
    sources: dict[tuple[str, str], dict[str, Any]],
    current: dict[tuple[str, str], tuple[str, dict[str, Any]]],
) -> None:
    """Require the final API read-back to contain correct reciprocal links."""
    if language_switcher_updates(sources, current):
        raise BloggerReleaseError("Blogger 언어 전환 링크 read-back 검증 실패")


class ReleaseClient:
    def __init__(self, client: BloggerClient) -> None:
        self.client = client

    def create_fresh_draft(self, payload: dict[str, object]) -> dict[str, object]:
        """Create a release draft without title-only reuse of another draft."""
        return self.client._request(
            "POST",
            f"/blogs/{self.client.blog_id}/posts",
            query={"isDraft": "true"},
            body=build_client_payload(payload),
        )

    def list_posts(self, state: str) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            query = {"fetchDrafts": "true", "status": state.lower(), "fetchBodies": "true", "maxResults": "100"}
            if page:
                query["pageToken"] = page
            response = self.client._request("GET", f"/blogs/{self.client.blog_id}/posts", query=query)
            items = response.get("items", [])
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise BloggerReleaseError(f"Blogger {state} 목록 형식이 올바르지 않습니다.")
            posts.extend(items)
            next_page = response.get("nextPageToken")
            if next_page is not None and not isinstance(next_page, str):
                raise BloggerReleaseError("Blogger page token 형식이 올바르지 않습니다.")
            page = next_page
            if not page:
                return posts

    def verify_staged_draft(self, post_id: str, source: dict[str, Any]) -> None:
        """Read an exact DRAFT back before it can be promoted."""
        matches = [post for post in self.list_posts("DRAFT") if str(post.get("id")) == post_id]
        if len(matches) != 1:
            raise BloggerReleaseError(f"Blogger staged draft read-back에서 글을 찾지 못했습니다: {post_id}")
        post = matches[0]
        if (post.get("title") != source["title"] or
                sorted(post.get("labels", [])) != source["labels"] or
                str(post.get("content", "")).strip() != str(source["content"]).strip()):
            raise BloggerReleaseError(f"Blogger staged draft read-back 실패: {post_id}")

    def current(self) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
        current = index_posts(self.list_posts("LIVE"), "LIVE")
        drafts = index_posts(self.list_posts("DRAFT"), "DRAFT")
        scheduled = index_posts(self.list_posts("SCHEDULED"), "SCHEDULED")
        overlap = (set(current) & set(drafts)) | (set(current) & set(scheduled)) | (set(drafts) & set(scheduled))
        if overlap:
            raise BloggerReleaseError(f"Blogger LIVE/DRAFT/SCHEDULED key가 동시에 존재합니다: {sorted(overlap)}")
        current.update(drafts)
        current.update(scheduled)
        return current

    def apply(self, sources: dict[tuple[str, str], dict[str, Any]], changes: list[dict[str, Any]]) -> dict[str, Any]:
        publish_times = {str(source["publish_date"]) for source in sources.values()}
        if len(publish_times) != 1:
            raise BloggerReleaseError("Blogger release 한·영 publish_date가 서로 다릅니다.")
        require_immediate_publish_time(next(iter(publish_times)))
        prepared: list[tuple[str, dict[str, Any]]] = []
        live_updates: list[tuple[str, dict[str, Any], tuple[str, str], str]] = []
        prepared_mutations: list[str] = []
        planned_switcher_keys: set[tuple[str, str]] = set()
        switchers_after_publish = False
        release_changes: list[dict[str, Any]] = []
        for change in changes:
            action = change["action"]
            if action == "update_language_switcher":
                planned_switcher_keys.add(tuple(change["key"]))
            elif action == "update_language_switchers_after_publish":
                switchers_after_publish = True
                planned_switcher_keys.update(tuple(key) for key in change["keys"])
            else:
                release_changes.append(change)
        if switchers_after_publish and planned_switcher_keys != set(sources):
            raise BloggerReleaseError("Blogger 언어 전환 링크 post-publish 계획이 승인 한·영 쌍과 일치하지 않습니다.")
        for change in release_changes:
            source = sources[tuple(change["key"])]
            payload = {"title": source["title"], "labels": source["labels"], "content": source["content"]}
            action = change["action"]
            try:
                if action == "create_draft_then_publish":
                    post = self.create_fresh_draft(payload)
                    prepared.append((str(post["id"]), source))
                    prepared_mutations.append(f"create:{source['language']}:{post['id']}")
                elif action == "update_draft_then_publish":
                    self.client.update_draft(str(change["id"]), payload)
                    prepared.append((str(change["id"]), source))
                    prepared_mutations.append(f"update:{source['language']}:{change['id']}")
                elif action == "publish_draft":
                    prepared.append((str(change["id"]), source))
                elif action == "update_live":
                    navigation = str(change.get("navigation", ""))
                    if navigation:
                        payload["content"] = str(payload["content"]).rstrip() + "\n\n" + navigation + "\n"
                    live_updates.append((str(change["id"]), payload, tuple(change["key"]), navigation))
                else:
                    raise BloggerReleaseError(f"지원하지 않는 release action: {action}")
            except BloggerApiError as error:
                raise BloggerReleaseError(
                    "Blogger 한·영 초안 준비가 부분 완료되었습니다. "
                    f"prepared={prepared_mutations}, failed={source['language']}; "
                    "공개 처리는 시작하지 않았으며 동일 release unit의 DRAFT 상태를 read-back 후 재시도하십시오."
                ) from error

        try:
            for post_id, source in prepared:
                self.verify_staged_draft(post_id, source)
        except (BloggerApiError, BloggerReleaseError) as error:
            raise BloggerReleaseError(
                "Blogger 한·영 초안 준비 read-back이 실패했습니다. 공개 처리는 시작하지 않았습니다."
            ) from error

        published_posts: list[dict[str, str]] = []
        failed_publish: dict[str, str] | None = None
        for post_id, source in prepared:
            try:
                failed_publish = {"id": post_id, "language": str(source["language"]), "action": "publish_draft"}
                self.client.publish_draft(post_id, str(source["publish_date"]))
                published_posts.append({"id": post_id, "language": str(source["language"])})
                failed_publish = None
            except BloggerApiError as error:
                published_languages = {item["language"] for item in published_posts}
                remaining = sorted({str(item["language"]) for _, item in prepared} - published_languages)
                raise BloggerReleaseError(
                    "Blogger 한·영 공개가 부분 완료되었습니다. "
                    f"published={published_posts}, failed={failed_publish}, remaining={remaining}; "
                    "승인 원본은 그대로 보존하고 실패한 언어만 동일 release unit으로 재시도하십시오."
                ) from error
        completed_live: list[dict[str, Any]] = []
        navigation_expectations = {key: navigation for _, _, key, navigation in live_updates if navigation}
        failed_live: dict[str, Any] | None = None
        try:
            for post_id, payload, key, _ in live_updates:
                failed_live = {"id": post_id, "key": key, "language": sources[key]["language"]}
                self.client.update_post(post_id, payload)
                completed_live.append(failed_live)
                failed_live = None
        except BloggerApiError as error:
            raise BloggerReleaseError(
                "Blogger 기존 LIVE 글 갱신이 부분 완료되었습니다. "
                f"prepared={prepared_mutations}, published={published_posts}, "
                f"completed={completed_live}, failed={failed_live}; "
                "승인 원본은 그대로 보존하고 위 ID를 read-back한 뒤 동일 release unit으로 재시도하십시오."
            ) from error

        try:
            current = self.verify(sources)
            for key, expected_navigation in navigation_expectations.items():
                actual = current.get(key)
                if actual is None:
                    raise BloggerReleaseError(f"Blogger 시리즈 목록 read-back 대상을 찾지 못했습니다: {key}")
                navigation = NAV_PATTERN.search(str(actual[1].get("content", "")))
                if navigation is None or navigation.group(0) != expected_navigation:
                    raise BloggerReleaseError(f"Blogger 시리즈 목록 보존 read-back 실패: {key}")
        except (BloggerApiError, BloggerReleaseError) as error:
            raise BloggerReleaseError(
                "Blogger 공개 후 read-back이 실패했습니다. "
                f"prepared={prepared_mutations}, published={published_posts}, live_completed={completed_live}; "
                "승인 원본은 그대로 보존하고 위 ID를 read-back한 뒤 동일 release unit으로 재시도하십시오."
            ) from error
        completed_switchers: list[dict[str, Any]] = []
        failed_switcher: dict[str, Any] | None = None
        try:
            switcher_updates = language_switcher_updates(sources, current)
            actual_switcher_keys = {
                next(source["key"] for source in sources.values() if source["language"] == update["language"])
                for update in switcher_updates
            }
            if actual_switcher_keys != planned_switcher_keys:
                raise BloggerReleaseError(
                    "Blogger 언어 전환 링크 실제 변경이 계획과 일치하지 않습니다: "
                    f"planned={sorted(planned_switcher_keys)}, actual={sorted(actual_switcher_keys)}"
                )
            for update in switcher_updates:
                source = next(item for item in sources.values() if item["language"] == update["language"])
                failed_switcher = {"id": update["id"], "language": update["language"]}
                updated = self.client.update_post(str(update["id"]), {
                    "title": source["title"],
                    "labels": source["labels"],
                    "content": update["content"],
                })
                if updated.get("content") != update["content"]:
                    raise BloggerReleaseError(f"Blogger 언어 전환 링크 write read-back 실패: {update['language']}")
                completed_switchers.append(failed_switcher)
                failed_switcher = None
        except (BloggerApiError, BloggerReleaseError) as error:
            raise BloggerReleaseError(
                "Blogger 언어 전환 링크 갱신이 부분 완료되었습니다. "
                f"prepared={prepared_mutations}, published={published_posts}, "
                f"live_completed={completed_live}, switcher_completed={completed_switchers}, failed={failed_switcher}; "
                "승인 원본은 그대로 보존하고 위 ID를 read-back한 뒤 동일 release unit으로 재시도하십시오."
            ) from error
        try:
            verify_language_switchers(sources, self.verify(sources))
        except (BloggerApiError, BloggerReleaseError) as error:
            raise BloggerReleaseError(
                "Blogger 언어 전환 링크 최종 read-back이 실패했습니다. "
                f"prepared={prepared_mutations}, published={published_posts}, "
                f"live_completed={completed_live}, switcher_completed={completed_switchers}; "
                "승인 원본은 그대로 보존하고 위 ID를 read-back한 뒤 동일 release unit으로 재시도하십시오."
            ) from error
        return {
            "prepared": prepared_mutations,
            "published": published_posts,
            "live_completed": completed_live,
            "switcher_completed": completed_switchers,
        }

    def verify(self, sources: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
        current = self.current()
        for key, source in sources.items():
            actual = current.get(key)
            if actual is None:
                raise BloggerReleaseError(f"Blogger read-back에서 글을 찾지 못했습니다: {key}")
            state, post = actual
            if state != "LIVE":
                raise BloggerReleaseError(f"Blogger read-back state가 LIVE가 아닙니다: {key}")
            if sorted(post.get("labels", [])) != source["labels"]:
                raise BloggerReleaseError(f"Blogger label read-back 실패: {key}")
            actual_content = strip_navigation(str(post.get("content", "")))
            actual_content = strip_language_switcher(actual_content)
            if actual_content.strip() != str(source["content"]).strip():
                raise BloggerReleaseError(f"Blogger 본문 read-back 실패: {key}")
        return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--image-urls", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--blog-id", default=DEFAULT_BLOG_ID)
    parser.add_argument("--token-path", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--client-secret-path", type=Path, default=DEFAULT_CLIENT_SECRET_PATH)
    parser.add_argument("--pages-base-url", default="https://oneplate-ai.github.io")
    parser.add_argument(
        "--require-pages-readback",
        action="store_true",
        help="과도기 옵션: Blogger 계획/발행 전 Pages URL을 추가 검증",
    )
    parser.add_argument("--apply", action="store_true", help="Blogger API에 실제 반영")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        approval = validate_approval(args.approval.resolve(), root)
        pages_urls = optional_pages_readback(
            approval,
            args.pages_base_url,
            required=args.require_pages_readback,
        )
        sources = release_sources(root, args.approval, args.image_urls)
        token = fresh_access_token(args.token_path, args.client_secret_path)
        release = ReleaseClient(BloggerClient(token, args.blog_id))
        changes = plan_release(sources, release.current())
        result: dict[str, Any] = {
            "mode": "apply" if args.apply else "plan",
            "sources": len(sources),
            "pages_readback_required": args.require_pages_readback,
            "pages_readback": pages_urls,
            "changes": changes,
        }
        if args.apply:
            mutation_state = release.apply(sources, changes)
            try:
                current = release.verify(sources)
                result["blogger_readback"] = verify_blogger_public_pair(sources, current)
            except (BloggerApiError, BloggerReleaseError) as error:
                raise BloggerReleaseError(
                    "Blogger apply 후 공개 URL 검증이 실패했습니다. "
                    f"mutations={mutation_state}; 위 ID를 read-back한 뒤 동일 release unit으로 재시도하십시오."
                ) from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, BloggerApiError, BloggerReleaseError) as error:
        print(f"Blogger release 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
