#!/usr/bin/env python3
import json
import unittest
from urllib.parse import urlparse, parse_qs

from blogger_publish import BloggerClient, build_client_payload


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, listed=None, created=None):
        self.listed = listed or []
        self.created = created or {
            "id": "new-id",
            "title": "새 글",
            "status": "DRAFT",
            "labels": ["weekly-ai-ko"],
        }
        self.calls = []

    def __call__(self, request, timeout=30):
        self.calls.append(request)
        url = request.full_url
        if request.method == "GET":
            return FakeResponse({"items": self.listed})
        if request.method == "POST" and "/publish?" in request.full_url:
            return FakeResponse({
                "id": "existing-id",
                "title": "새 글",
                "status": "LIVE",
                "url": "https://oneplate-ai.blogspot.com/2026/10/post.html",
            })
        if request.method == "PUT":
            return FakeResponse({
                "id": "existing-id",
                "title": "새 글",
                "status": "DRAFT",
                "labels": ["weekly-ai-ko"],
                "content": "<p>갱신 본문</p>",
            })
        if request.method == "POST":
            return FakeResponse(self.created)
        raise AssertionError(request.method)


class BloggerPublishTests(unittest.TestCase):
    def test_draft_payload_excludes_adapter_only_fields(self):
        payload = {
            "title": "새 글",
            "content": "<p>본문</p>",
            "labels": ["weekly-ai-ko"],
            "is_draft": True,
            "source_permalink": "/posts/example.html",
        }
        self.assertEqual(
            build_client_payload(payload),
            {"title": "새 글", "content": "<p>본문</p>", "labels": ["weekly-ai-ko"]},
        )

    def test_create_uses_draft_endpoint_and_reads_back(self):
        opener = FakeOpener(created={
            "id": "new-id",
            "title": "새 글",
            "status": "DRAFT",
            "labels": ["weekly-ai-ko"],
        })
        client = BloggerClient("token", "blog-id", opener=opener)
        post = client.create_draft({
            "title": "새 글",
            "content": "<p>본문</p>",
            "labels": ["weekly-ai-ko"],
            "is_draft": True,
        })
        self.assertEqual(post["status"], "DRAFT")
        post_request = opener.calls[-1]
        self.assertEqual(post_request.method, "POST")
        self.assertEqual(parse_qs(urlparse(post_request.full_url).query), {"isDraft": ["true"]})

    def test_existing_title_is_returned_without_duplicate_post(self):
        opener = FakeOpener(listed=[{
            "id": "existing-id",
            "title": "새 글",
            "status": "DRAFT",
        }])
        client = BloggerClient("token", "blog-id", opener=opener)
        post = client.create_draft({
            "title": "새 글",
            "content": "<p>본문</p>",
            "labels": ["weekly-ai-ko"],
            "is_draft": True,
        })
        self.assertEqual(post["id"], "existing-id")
        self.assertTrue(all(call.method == "GET" for call in opener.calls))

    def test_update_existing_draft_uses_put_endpoint(self):
        opener = FakeOpener(listed=[{
            "id": "existing-id",
            "title": "새 글",
            "status": "DRAFT",
        }])
        client = BloggerClient("token", "blog-id", opener=opener)
        post = client.update_draft("existing-id", {
            "title": "새 글",
            "content": "<p>갱신 본문</p>",
            "labels": ["weekly-ai-ko"],
            "is_draft": True,
        })
        self.assertEqual(post["id"], "existing-id")
        self.assertEqual(opener.calls[-1].method, "PUT")

    def test_publish_draft_uses_publish_endpoint_and_date(self):
        opener = FakeOpener()
        client = BloggerClient("token", "blog-id", opener=opener)
        post = client.publish_draft("existing-id", "2026-10-03T00:00:00+09:00")
        self.assertEqual(post["status"], "LIVE")
        self.assertEqual(opener.calls[-1].method, "POST")
        self.assertIn("publishDate=2026-10-03T00%3A00%3A00%2B09%3A00", opener.calls[-1].full_url)

    def test_update_live_post_uses_put_without_draft_flag(self):
        opener = FakeOpener()
        client = BloggerClient("token", "blog-id", opener=opener)
        post = client.update_post("existing-id", {
            "title": "새 글",
            "content": "<nav>언어</nav><p>본문</p>",
            "labels": ["weekly-ai-ko"],
        })
        self.assertEqual(post["status"], "DRAFT")
        self.assertEqual(opener.calls[-1].method, "PUT")
        self.assertNotIn("isDraft=true", opener.calls[-1].full_url)


if __name__ == "__main__":
    unittest.main()
