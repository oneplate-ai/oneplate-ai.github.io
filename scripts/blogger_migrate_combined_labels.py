#!/usr/bin/env python3
"""Normalize public Blogger post labels to one combined series-language label.

Default mode is read-only and prints the exact per-post plan.  ``--apply``
updates LIVE posts only, preserving title, body, URL, publication time, and any
non-public management labels such as ``media-staging``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from blogger_adapter import combined_label, label_identity
from blogger_full_parity_sync import BLOG_ID, CLIENT_SECRET_PATH, TOKEN_PATH, BloggerClient, fresh_access_token

PUBLIC_SERIES = {"today-ai-bite", "weekly-ai", "easy-ai"}
LEGACY_PUBLIC_LABELS = PUBLIC_SERIES | {"ko", "en"}


class LabelMigrationError(RuntimeError):
    pass


def desired_labels(post: dict[str, Any]) -> list[str]:
    """Replace only public series/language labels; retain unrelated management labels."""
    labels = post.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise LabelMigrationError(f"Blogger label 형식이 올바르지 않습니다: {post.get('id')}")
    identity = label_identity(labels)
    if identity is not None:
        series, language = identity
    else:
        legacy_series = [label for label in labels if label in PUBLIC_SERIES]
        legacy_languages = [label for label in labels if label in {"ko", "en"}]
        if len(legacy_series) != 1 or len(legacy_languages) != 1:
            raise LabelMigrationError(f"Blogger 공개 글의 series/language을 판별할 수 없습니다: {post.get('id')}")
        series, language = legacy_series[0], legacy_languages[0]
    if series not in PUBLIC_SERIES:
        raise LabelMigrationError(f"지원하지 않는 공개 series입니다: {post.get('id')} ({series})")
    retained = [label for label in labels if label not in LEGACY_PUBLIC_LABELS and label_identity([label]) is None]
    return sorted(set(retained + [combined_label(series, language)]))


def plan(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for post in posts:
        expected = desired_labels(post)
        actual = sorted(post.get("labels", []))
        if actual != expected:
            changes.append({
                "id": str(post.get("id", "")),
                "title": str(post.get("title", "")),
                "from_labels": actual,
                "to_labels": expected,
            })
    return changes


def update_labels(client: BloggerClient, post: dict[str, Any], labels: list[str]) -> None:
    content = str(post.get("content", ""))
    body = {"title": str(post.get("title", "")), "labels": labels, "content": content}
    client.request("PUT", f"/blogs/{client.blog_id}/posts/{post['id']}", body=body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--blog-id", default=BLOG_ID)
    parser.add_argument("--token-path", type=Path, default=TOKEN_PATH)
    parser.add_argument("--client-secret-path", type=Path, default=CLIENT_SECRET_PATH)
    args = parser.parse_args(argv)
    try:
        client = BloggerClient(fresh_access_token(args.token_path, args.client_secret_path), args.blog_id)
        posts = client.list_posts("LIVE")
        changes = plan(posts)
        print(json.dumps({"mode": "apply" if args.apply else "plan", "live_posts": len(posts), "changes": changes}, ensure_ascii=False, indent=2))
        if not args.apply:
            return 0
        by_id = {str(post["id"]): post for post in posts}
        for change in changes:
            update_labels(client, by_id[change["id"]], list(change["to_labels"]))
        final = {str(post["id"]): post for post in client.list_posts("LIVE")}
        for change in changes:
            actual = sorted(final[change["id"]].get("labels", []))
            if actual != change["to_labels"]:
                raise LabelMigrationError(f"Blogger label read-back 실패: {change['id']}")
        print(json.dumps({"applied": len(changes), "live_posts": len(final), "label_readback": "ok"}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, LabelMigrationError, json.JSONDecodeError) as error:
        print(f"Blogger 결합 label 정리 실패: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
