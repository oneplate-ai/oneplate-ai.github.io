#!/usr/bin/env python3
"""Make Blogger posts match the canonical GitHub Pages source inventory.

`_posts/` is canonical for duplicated articles. A draft is canonical only when
no same title/language article exists under `_posts/`. Default mode is read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from blogger_adapter import build_blogger_payload
from blogger_series_navigation import NAV_PATTERN

BLOGGER_API = "https://www.googleapis.com/blogger/v3"
BLOG_ID = "4141805834166693370"
TOKEN_PATH = Path("/root/.hermes/credentials/blogger-token.json")
CLIENT_SECRET_PATH = Path("/root/.hermes/credentials/blogger-client-secret.json")


class ParityError(RuntimeError):
    pass


def target_state(source: dict[str, Any]) -> str:
    return str(source["state"])


def canonical_sources(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    canonical: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = tuple(record["key"])
        existing = canonical.get(key)
        if existing is None or (existing["state"] == "DRAFT" and record["state"] == "LIVE"):
            canonical[key] = record
    return canonical


def apply_blogger_image_urls(content: str, image_urls: dict[str, str]) -> str:
    updated = content
    for old_url, new_url in image_urls.items():
        pattern = re.compile(r'(<img\b[^>]*\bsrc=["\'])' + re.escape(old_url) + r'(["\'])', re.I)
        updated = pattern.sub(lambda match: match.group(1) + new_url + match.group(2), updated)
    return updated


def strip_navigation(content: str) -> str:
    return NAV_PATTERN.sub("\n", content).strip()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _client_credentials(path: Path) -> tuple[str, str]:
    config_file = json.loads(path.read_text(encoding="utf-8"))
    config = config_file.get("installed") or config_file.get("web")
    if not isinstance(config, dict):
        raise ParityError("OAuth client 설정을 읽을 수 없습니다.")
    client_id, client_secret = config.get("client_id"), config.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise ParityError("OAuth client 설정이 불완전합니다.")
    return client_id, client_secret


def fresh_access_token(token_path: Path, client_secret_path: Path) -> str:
    token_data = json.loads(token_path.read_text(encoding="utf-8"))
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ParityError("OAuth refresh token이 없습니다.")
    client_id, client_secret = _client_credentials(client_secret_path)
    data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    with urlopen(Request("https://oauth2.googleapis.com/token", data=data, method="POST"), timeout=30) as response:
        access_token = json.loads(response.read().decode("utf-8")).get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ParityError("Blogger access token 갱신 실패")
    return access_token


class BloggerClient:
    def __init__(self, token: str, blog_id: str) -> None:
        self.token, self.blog_id = token, blog_id

    def request(self, method: str, path: str, *, query: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BLOGGER_API}{path}"
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise ParityError(f"Blogger API {method} {path} 요청 실패: {error}") from error

    def list_posts(self, state: str) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            query = {"fetchDrafts": "true", "status": state.lower(), "fetchBodies": "true", "maxResults": "100"}
            if page:
                query["pageToken"] = page
            response = self.request("GET", f"/blogs/{self.blog_id}/posts", query=query)
            posts.extend(response.get("items", []))
            page = response.get("nextPageToken")
            if not page:
                return posts

    def update(self, post: dict[str, Any], state: str, content: str) -> None:
        body = {"title": post["title"], "labels": post.get("labels", []), "content": content}
        query = {"isDraft": "true"} if state == "DRAFT" else None
        self.request("PUT", f"/blogs/{self.blog_id}/posts/{post['id']}", query=query, body=body)

    def publish(self, post_id: str, publish_date: str) -> None:
        self.request("POST", f"/blogs/{self.blog_id}/posts/{post_id}/publish", query={"publishDate": publish_date})

    def revert(self, post_id: str) -> None:
        self.request("POST", f"/blogs/{self.blog_id}/posts/{post_id}/revert")


def source_records(root: Path, image_urls: dict[str, str]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    for directory, state in (("_posts", "LIVE"), ("drafts", "DRAFT")):
        for path in sorted((root / directory).glob("*.md")):
            source = path.read_text(encoding="utf-8")
            try:
                payload = build_blogger_payload(source, public_base_url="https://oneplate-ai.github.io", draft=state == "DRAFT")
            except ValueError:
                skipped.append(str(path.relative_to(root)))
                continue
            labels = sorted(payload.get("labels", []))
            language = next((label for label in labels if label in {"ko", "en"}), None)
            date = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})\s*$", source, re.M)
            if language is None or date is None:
                raise ParityError(f"원본 메타데이터 부족: {path}")
            records.append({
                "key": (str(payload["title"]), language),
                "path": str(path.relative_to(root)),
                "state": state,
                "labels": labels,
                "content": apply_blogger_image_urls(str(payload["content"]), image_urls),
                "publish_date": date.group(1) + "T12:00:00+09:00",
            })
    return canonical_sources(records), skipped


def index_posts(posts: list[dict[str, Any]], state: str) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for post in posts:
        labels = sorted(post.get("labels", []))
        language = next((label for label in labels if label in {"ko", "en"}), None)
        if language is None:
            raise ParityError(f"Blogger 글의 언어 label 누락: {post.get('id')}")
        key = (str(post["title"]), language)
        if key in indexed:
            raise ParityError(f"Blogger 글 title/language 중복: {key}")
        indexed[key] = post
    return indexed


def plan(sources: dict[tuple[str, str], dict[str, Any]], current: dict[tuple[str, str], tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, source in sorted(sources.items()):
        actual = current.get(key)
        if actual is None:
            raise ParityError(f"Blogger 대응 글 누락: {key}")
        state, post = actual
        if sorted(post.get("labels", [])) != source["labels"]:
            raise ParityError(f"Blogger label 불일치: {key}")
        compare_actual = strip_navigation(str(post.get("content", ""))) if state == "LIVE" else str(post.get("content", "")).strip()
        content_change = compare_actual != str(source["content"]).strip()
        state_change = state != target_state(source)
        if content_change or state_change:
            changes.append({
                "key": key,
                "id": str(post["id"]),
                "title": post["title"],
                "from_state": state,
                "to_state": target_state(source),
                "content_change": content_change,
                "source_path": source["path"],
            })
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--image-urls", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        stored_urls = json.loads(args.image_urls.read_text(encoding="utf-8"))
        image_urls = {f"https://oneplate-ai.github.io/assets/images/{name}": url for name, url in stored_urls.items()}
        sources, skipped = source_records(root, image_urls)
        client = BloggerClient(fresh_access_token(TOKEN_PATH, CLIENT_SECRET_PATH), BLOG_ID)
        live, drafts = client.list_posts("LIVE"), client.list_posts("DRAFT")
        current = {(key): ("LIVE", post) for key, post in index_posts(live, "LIVE").items()}
        current.update({(key): ("DRAFT", post) for key, post in index_posts(drafts, "DRAFT").items()})
        if set(current) != set(sources):
            raise ParityError(f"원본/Blogger title-language 집합 불일치: source={len(sources)} blogger={len(current)}")
        changes = plan(sources, current)
        print(json.dumps({"mode": "apply" if args.apply else "plan", "canonical_sources": len(sources), "skipped_non_post_markdown": skipped, "changes": changes}, ensure_ascii=False, indent=2))
        if not args.apply:
            return 0
        for change in changes:
            key = tuple(change["key"])
            source = sources[key]
            old_state, post = current[key]
            desired_state = target_state(source)
            if old_state == "DRAFT" and desired_state == "LIVE":
                if change["content_change"]:
                    client.update(post, "DRAFT", source["content"])
                client.publish(str(post["id"]), source["publish_date"])
            elif old_state == "LIVE" and desired_state == "DRAFT":
                client.revert(str(post["id"]))
                refreshed = index_posts(client.list_posts("DRAFT"), "DRAFT")[key]
                client.update(refreshed, "DRAFT", source["content"])
            elif change["content_change"]:
                client.update(post, desired_state, source["content"])
        # Exact source verification occurs before the separate navigation renderer re-adds its marked block.
        live, drafts = client.list_posts("LIVE"), client.list_posts("DRAFT")
        final = {(key): ("LIVE", post) for key, post in index_posts(live, "LIVE").items()}
        final.update({(key): ("DRAFT", post) for key, post in index_posts(drafts, "DRAFT").items()})
        if set(final) != set(sources):
            raise ParityError("반영 후 원본/Blogger 집합 불일치")
        for key, source in sources.items():
            state, post = final[key]
            if state != target_state(source) or sorted(post.get("labels", [])) != source["labels"]:
                raise ParityError(f"상태/label read-back 실패: {key}")
            actual = strip_navigation(str(post.get("content", ""))) if state == "LIVE" else str(post.get("content", "")).strip()
            if actual != str(source["content"]).strip():
                raise ParityError(f"본문 read-back 실패: {key}")
        print(json.dumps({"applied": len(changes), "live": len(live), "draft": len(drafts), "source_content_readback": "ok"}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ParityError, json.JSONDecodeError) as error:
        print(f"Blogger 완전 동기화 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
