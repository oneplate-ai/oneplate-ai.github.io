#!/usr/bin/env python3
"""Synchronize Blogger series navigation, legacy language-nav removal, and restored Page image URLs.

Default mode is read-only.  Pass --apply only after reviewing the exact plan.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from blogger_adapter import label_identity, remove_language_switcher

BLOGGER_API = "https://www.googleapis.com/blogger/v3"
DEFAULT_BLOG_ID = "4141805834166693370"
DEFAULT_TOKEN_PATH = Path("/root/.hermes/credentials/blogger-token.json")
DEFAULT_CLIENT_SECRET_PATH = Path("/root/.hermes/credentials/blogger-client-secret.json")
NAV_START = "<!-- oneplate-series-navigation:start -->"
NAV_END = "<!-- oneplate-series-navigation:end -->"
NAV_PATTERN = re.compile(
    re.escape(NAV_START) + r".*?" + re.escape(NAV_END), re.DOTALL
)
SERIES_TITLES = {
    ("ko", "today-ai-bite"): "오늘 AI 한입",
    ("en", "today-ai-bite"): "Today AI Bite",
    ("ko", "weekly-ai"): "주간 AI 한 접시",
    ("en", "weekly-ai"): "Weekly AI One Plate",
    ("ko", "easy-ai"): "쉽게 먹는 AI",
    ("en", "easy-ai"): "Easy AI",
}
LANGUAGES = {"ko", "en"}
SERIES = {"today-ai-bite", "weekly-ai", "easy-ai"}


class BloggerApiError(RuntimeError):
    pass


def language_and_series(post: dict[str, Any]) -> tuple[str, str]:
    identity = label_identity(post.get("labels", []))
    if identity is None:
        raise ValueError(f"글의 결합 series-language label이 하나여야 합니다: {post.get('id')}")
    series, language = identity
    if series not in SERIES:
        raise ValueError(f"지원하지 않는 series label입니다: {post.get('id')}")
    return language, series


def render_series_navigation(post: dict[str, Any], posts: list[dict[str, Any]]) -> str:
    language, series = language_and_series(post)
    title = SERIES_TITLES[(language, series)]
    matching = [
        candidate for candidate in posts
        if language_and_series(candidate) == (language, series) and candidate.get("published")
    ]
    matching.sort(key=lambda candidate: str(candidate.get("published", "")), reverse=True)
    if not matching:
        raise ValueError(f"공개된 같은 시리즈 글이 없습니다: {post.get('id')}")
    items: list[str] = []
    for candidate in matching:
        candidate_title = html.escape(str(candidate.get("title", "")))
        url = html.escape(str(candidate.get("url", "")), quote=True)
        current = ' aria-current="page"' if str(candidate.get("id")) == str(post.get("id")) else ""
        items.append(f'<li><a href="{url}"{current}>{candidate_title}</a></li>')
    return (
        f'{NAV_START}\n'
        f'<section aria-label="{html.escape(title)} 글 목록" class="oneplate-series-navigation">\n'
        f'  <h2>{html.escape(title)} 글 목록</h2>\n'
        f'  <ol>\n    ' + "\n    ".join(items) + '\n  </ol>\n'
        f'</section>\n{NAV_END}'
    )


def replace_navigation(content: str, navigation: str) -> str:
    if NAV_PATTERN.search(content):
        return NAV_PATTERN.sub(navigation, content, count=1)
    return content.rstrip() + "\n\n" + navigation + "\n"


def language_pair_urls(posts: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Map every live post ID to its exact Korean/English counterpart URLs."""
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for post in posts:
        if not post.get("published"):
            continue
        language, series = language_and_series(post)
        published = post.get("published")
        url = post.get("url")
        if not isinstance(published, str) or not isinstance(url, str) or not url:
            raise ValueError(f"공개 글의 published/url 메타데이터가 없습니다: {post.get('id')}")
        group = groups.setdefault((series, published), {})
        if language in group:
            raise ValueError(f"한·영 대응 글이 중복됩니다: series={series}, published={published}, lang={language}")
        group[language] = post

    pairs: dict[str, tuple[str, str]] = {}
    for (series, published), group in groups.items():
        if set(group) != {"ko", "en"}:
            raise ValueError(f"한·영 대응 글이 불완전합니다: series={series}, published={published}")
        ko_url, en_url = str(group["ko"]["url"]), str(group["en"]["url"])
        pairs[str(group["ko"]["id"])] = (ko_url, en_url)
        pairs[str(group["en"]["id"])] = (ko_url, en_url)
    return pairs


def navigation_block(content: str) -> str | None:
    match = NAV_PATTERN.search(content)
    return match.group(0) if match else None


def apply_image_url_map(content: str, image_urls: dict[str, str]) -> tuple[str, bool]:
    updated = content
    for old_url, new_url in image_urls.items():
        pattern = re.compile(r'(<img\b[^>]*\bsrc=["\'])' + re.escape(old_url) + r'(["\'])', re.I)
        updated = pattern.sub(lambda match: match.group(1) + new_url + match.group(2), updated)
    return updated, updated != content


def _client_credentials(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    config = data.get("installed") or data.get("web")
    if not isinstance(config, dict):
        raise ValueError("OAuth client 설정을 읽을 수 없습니다.")
    client_id, client_secret = config.get("client_id"), config.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise ValueError("OAuth client 설정에 client_id/client_secret이 필요합니다.")
    return client_id, client_secret


def fresh_access_token(token_path: Path, client_secret_path: Path) -> str:
    token = json.loads(token_path.read_text(encoding="utf-8"))
    refresh_token = token.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ValueError("OAuth refresh_token이 없습니다.")
    client_id, client_secret = _client_credentials(client_secret_path)
    form = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    request = Request("https://oauth2.googleapis.com/token", data=form, method="POST")
    with urlopen(request, timeout=30) as response:
        access_token = json.loads(response.read().decode("utf-8")).get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise BloggerApiError("새 Blogger access token을 받지 못했습니다.")
    return access_token


class BloggerClient:
    def __init__(self, access_token: str, blog_id: str) -> None:
        self.access_token = access_token
        self.blog_id = blog_id

    def request(self, method: str, path: str, *, query: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BLOGGER_API}{path}"
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # API response messages are not secrets, but keep output concise.
            raise BloggerApiError(f"Blogger API {method} {path} 요청 실패: {error}") from error

    def list_resource(self, resource: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            query = {"fetchBodies": "true", "maxResults": "100"}
            if resource == "posts":
                query["fetchDrafts"] = "true"
            if page_token:
                query["pageToken"] = page_token
            response = self.request("GET", f"/blogs/{self.blog_id}/{resource}", query=query)
            items.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def patch(self, resource: str, resource_id: str, content: str) -> dict[str, Any]:
        return self.request(
            "PATCH", f"/blogs/{self.blog_id}/{resource}/{resource_id}", body={"content": content}
        )


def image_url_map(urls_path: Path) -> dict[str, str]:
    saved_urls = json.loads(urls_path.read_text(encoding="utf-8"))
    if not isinstance(saved_urls, dict):
        raise ValueError("이미지 URL 매핑 파일 형식이 올바르지 않습니다.")
    return {
        f"https://oneplate-ai.github.io/assets/images/{filename}": str(url)
        for filename, url in saved_urls.items()
    }


def plan_changes(posts: list[dict[str, Any]], pages: list[dict[str, Any]], image_urls: dict[str, str]) -> tuple[list[tuple[dict[str, Any], str]], list[tuple[dict[str, Any], str]]]:
    post_changes: list[tuple[dict[str, Any], str]] = []
    language_pair_urls(posts)  # Retain existing identity/pair validation.
    for post in posts:
        if not post.get("published"):
            continue
        language_and_series(post)
        expected = replace_navigation(str(post.get("content", "")), render_series_navigation(post, posts))
        expected = remove_language_switcher(expected)
        if expected != post.get("content"):
            post_changes.append((post, expected))
    page_changes: list[tuple[dict[str, Any], str]] = []
    for page in pages:
        expected, changed = apply_image_url_map(str(page.get("content", "")), image_urls)
        if changed:
            page_changes.append((page, expected))
    return post_changes, page_changes


def verify_post(before: dict[str, Any], after: dict[str, Any], expected_content: str) -> None:
    for key in ("id", "title", "labels", "published", "url"):
        if after.get(key) != before.get(key):
            raise BloggerApiError(f"글 {before.get('id')}의 {key} 값이 바뀌었습니다.")
    if after.get("content") != expected_content:
        raise BloggerApiError(f"글 {before.get('id')} 본문 read-back 검증 실패")


def verify_page(before: dict[str, Any], after: dict[str, Any], expected_content: str) -> None:
    for key in ("id", "title", "url"):
        if after.get(key) != before.get(key):
            raise BloggerApiError(f"페이지 {before.get('id')}의 {key} 값이 바뀌었습니다.")
    if after.get("content") != expected_content:
        raise BloggerApiError(f"페이지 {before.get('id')} 본문 read-back 검증 실패")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="검토한 계획을 실제 Blogger에 반영")
    parser.add_argument("--blog-id", default=DEFAULT_BLOG_ID)
    parser.add_argument("--token-path", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--client-secret-path", type=Path, default=DEFAULT_CLIENT_SECRET_PATH)
    parser.add_argument("--image-urls", type=Path, required=True, help="Blogger 저장 이미지 URL 매핑 JSON")
    args = parser.parse_args(argv)
    try:
        client = BloggerClient(fresh_access_token(args.token_path, args.client_secret_path), args.blog_id)
        posts = client.list_resource("posts")
        pages = client.list_resource("pages")
        post_changes, page_changes = plan_changes(posts, pages, image_url_map(args.image_urls))
        switcher_changes = [
            {"id": post["id"], "title": post["title"]}
            for post, expected in post_changes
            if remove_language_switcher(str(post.get("content", ""))) != str(post.get("content", ""))
        ]
        navigation_changes = [
            {"id": post["id"], "title": post["title"]}
            for post, expected in post_changes
            if navigation_block(str(post.get("content", ""))) != navigation_block(expected)
        ]
        summary = {
            "mode": "apply" if args.apply else "plan",
            "posts_total": len(posts),
            "live_post_content_changes": [
                {"id": post["id"], "title": post["title"]} for post, _ in post_changes
            ],
            "live_post_language_switcher_changes": switcher_changes,
            "live_post_navigation_changes": navigation_changes,
            "page_image_changes": [
                {"id": page["id"], "title": page["title"]} for page, _ in page_changes
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not args.apply:
            return 0
        for post, expected in post_changes:
            verify_post(post, client.patch("posts", str(post["id"]), expected), expected)
        for page, expected in page_changes:
            verify_page(page, client.patch("pages", str(page["id"]), expected), expected)
        print(json.dumps({"applied_posts": len(post_changes), "applied_pages": len(page_changes)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, BloggerApiError, json.JSONDecodeError) as error:
        print(f"시리즈 목록 동기화 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
