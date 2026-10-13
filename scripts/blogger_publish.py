#!/usr/bin/env python3
"""Create Blogger drafts from approved Jekyll posts; never publishes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from blogger_adapter import blogger_request_body, build_blogger_payload
from publication_gate import GateError, validate_approval


BLOGGER_API = "https://www.googleapis.com/blogger/v3"


class BloggerApiError(RuntimeError):
    """A Blogger API request failed."""


def build_client_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return only fields accepted by the Blogger posts resource."""
    return blogger_request_body(payload)


class BloggerClient:
    def __init__(
        self,
        access_token: str,
        blog_id: str,
        *,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not access_token:
            raise ValueError("Blogger access token이 비어 있습니다.")
        if not blog_id:
            raise ValueError("Blogger blog ID가 필요합니다.")
        self.access_token = access_token
        self.blog_id = blog_id
        self.opener = opener

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{BLOGGER_API}{path}"
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError) as error:
            detail = getattr(error, "reason", str(error))
            raise BloggerApiError(f"Blogger API {method} 요청 실패: {detail}") from error

    def list_drafts(self) -> list[dict[str, object]]:
        result = self._request(
            "GET",
            f"/blogs/{self.blog_id}/posts",
            query={
                "fetchDrafts": "true",
                "status": "draft",
                "fetchBodies": "true",
                "maxResults": "50",
            },
        )
        return cast(list[dict[str, object]], result.get("items", []))

    def find_draft(self, title: str) -> dict[str, object] | None:
        return next((post for post in self.list_drafts() if post.get("title") == title), None)

    def create_draft(self, payload: dict[str, object]) -> dict[str, object]:
        existing = self.find_draft(str(payload["title"]))
        if existing is not None:
            return existing
        return self._request(
            "POST",
            f"/blogs/{self.blog_id}/posts",
            query={"isDraft": "true"},
            body=build_client_payload(payload),
        )

    def update_draft(self, post_id: str, payload: dict[str, object]) -> dict[str, object]:
        """Replace an existing draft only when the caller explicitly allows it."""
        return self._request(
            "PUT",
            f"/blogs/{self.blog_id}/posts/{post_id}",
            query={"isDraft": "true"},
            body=build_client_payload(payload),
        )

    def update_post(self, post_id: str, payload: dict[str, object]) -> dict[str, object]:
        """Replace a published post while preserving its public state."""
        return self._request(
            "PUT",
            f"/blogs/{self.blog_id}/posts/{post_id}",
            body=build_client_payload(payload),
        )

    def publish_draft(self, post_id: str, publish_date: str | None = None) -> dict[str, object]:
        """Publish one verified draft through Blogger's explicit publish endpoint."""
        query = {"publishDate": publish_date} if publish_date else None
        return self._request(
            "POST",
            f"/blogs/{self.blog_id}/posts/{post_id}/publish",
            query=query,
        )

    def read_back_draft(self, post_id: str) -> dict[str, object]:
        # Blogger's single-post endpoint does not reliably expose draft posts;
        # list with status=draft and match the exact post ID instead.
        for post in self.list_drafts():
            if str(post.get("id")) == str(post_id):
                return post
        raise BloggerApiError(f"생성된 Blogger 초안을 다시 찾지 못했습니다: {post_id}")


def _load_token(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"토큰 파일을 읽을 수 없습니다: {path}") from error
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError(f"토큰 파일에 access_token이 없습니다: {path}")
    return token


def require_approved_bilingual_sources(
    root: Path,
    approval_path: Path,
    source_paths: list[Path],
) -> dict[str, object]:
    """Require exactly the bilingual draft pair covered by a valid approval."""
    try:
        approval = validate_approval(approval_path.resolve(), root.resolve())
    except (GateError, OSError, ValueError) as error:
        raise ValueError(f"GitHub Pages 승인 게이트를 통과하지 못했습니다: {error}") from error
    if len(source_paths) != 2:
        raise ValueError("Blogger 전송은 승인된 한국어·영어 초안 2개가 필요합니다.")
    approved_paths = {
        (root / item["exact_draft_path"]).resolve()
        for item in approval["drafts"]
    }
    actual_paths = {path.resolve() for path in source_paths}
    if actual_paths != approved_paths:
        raise ValueError(
            "Blogger source가 승인 기록의 exact_draft_path와 다릅니다. "
            "승인된 drafts/ 파일을 그대로 지정해야 합니다."
        )
    return approval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, help="Jekyll 게시글 경로; 여러 번 지정 가능")
    parser.add_argument("--approval", type=Path, required=True, help=".publishing-approvals/의 schema v2 승인 기록")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--blog-id", required=True, help="Blogger blog ID")
    parser.add_argument("--token-path", type=Path, default=Path("/root/.hermes/credentials/blogger-token.json"))
    parser.add_argument("--base-url", default="https://oneplate-ai.github.io")
    parser.add_argument("--draft", action="store_true", help="초안 생성 모드; 현재 유일하게 허용되는 모드")
    parser.add_argument(
        "--sync-existing",
        action="store_true",
        help="같은 제목의 기존 Blogger 초안을 승인된 source 내용으로 갱신",
    )
    args = parser.parse_args(argv)
    if not args.draft:
        parser.error("안전상 --draft만 지원합니다. Blogger 공개 발행은 구현하지 않았습니다.")

    try:
        root = args.root.resolve()
        source_paths = [Path(value) for value in args.source]
        approval = require_approved_bilingual_sources(root, args.approval, source_paths)
        client = BloggerClient(_load_token(args.token_path), args.blog_id)
        results = []
        for source_value in args.source:
            source = Path(source_value)
            payload = build_blogger_payload(
                source.read_text(encoding="utf-8"),
                public_base_url=args.base_url,
                draft=True,
            )
            existing = client.find_draft(str(payload["title"]))
            if existing and existing.get("content") != payload["content"]:
                if not args.sync_existing:
                    raise BloggerApiError(
                        f"기존 Blogger 초안 내용이 승인 source와 다릅니다: {source} "
                        "(--sync-existing 없이는 덮어쓰지 않습니다.)"
                    )
                post = client.update_draft(str(existing["id"]), payload)
                updated = True
            else:
                post = existing or client.create_draft(payload)
                updated = False
            post_id = str(post.get("id", ""))
            readback = client.read_back_draft(post_id)
            if readback.get("title") != payload["title"] or readback.get("status") != "DRAFT":
                raise BloggerApiError(f"초안 read-back 검증 실패: {source}")
            if readback.get("content") != payload["content"]:
                raise BloggerApiError(f"초안 본문 read-back 검증 실패: {source}")
            results.append({
                "source": str(source),
                "created": existing is None,
                "updated": updated,
                "post_id": post_id,
                "title": readback.get("title"),
                "status": readback.get("status"),
                "labels": readback.get("labels", []),
            })
        print(json.dumps({
            "mode": "draft",
            "approval": approval["publication_unit"],
            "results": results,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, BloggerApiError) as error:
        print(f"Blogger 초안 발행 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
