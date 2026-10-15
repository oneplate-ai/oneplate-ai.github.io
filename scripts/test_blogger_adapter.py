#!/usr/bin/env python3
import unittest

from blogger_adapter import build_blogger_payload, inject_language_switcher


POST = """---
title: AI를 쉽게 이해하는 글
description: "짧은 설명"
series: easy-ai
lang: ko
permalink: /posts/2026-09-03-easy-ai-01.html
published: true
layout: post
---
<p>본문입니다.</p>
<figure><img src="{{ '/assets/images/example.svg' | relative_url }}" alt="예시"></figure>
"""

DUPLICATE_TITLE_POST = """---
title: 2026년 10월 3일 - 2026년 10월 9일
series: weekly-ai
lang: ko
---
<p class="post-kicker">이번 주 AI 한 접시</p>
<h1>2026년 10월 3일 - 2026년 10월 9일</h1>
<p class="summary">요약입니다.</p>
"""


class BloggerAdapterTests(unittest.TestCase):
    def test_builds_draft_payload_from_jekyll_post(self):
        payload = build_blogger_payload(
            POST,
            public_base_url="https://oneplate-ai.github.io",
            draft=True,
        )

        self.assertEqual(payload["title"], "AI를 쉽게 이해하는 글")
        self.assertEqual(payload["labels"], ["easy-ai-ko"])
        self.assertTrue(payload["is_draft"])
        self.assertIn("<p>본문입니다.</p>", payload["content"])
        self.assertIn(
            'src="https://oneplate-ai.github.io/assets/images/example.svg"',
            payload["content"],
        )
        self.assertNotIn("{{", payload["content"])

    def test_rejects_missing_title(self):
        with self.assertRaises(ValueError):
            build_blogger_payload(
                POST.replace("title: AI를 쉽게 이해하는 글\n", ""),
                public_base_url="https://oneplate-ai.github.io",
            )

    def test_removes_repeated_leading_h1_matching_post_title(self):
        payload = build_blogger_payload(
            DUPLICATE_TITLE_POST,
            public_base_url="https://oneplate-ai.github.io",
        )
        self.assertNotIn("<h1>2026년 10월 3일 - 2026년 10월 9일</h1>", payload["content"])
        self.assertIn('<p class="post-kicker">이번 주 AI 한 접시</p>', payload["content"])
        self.assertIn('<p class="summary">요약입니다.</p>', payload["content"])

    def test_legacy_injection_is_marker_free_noop(self):
        content = ' \n<p>본문입니다.</p>\n '
        self.assertEqual(inject_language_switcher(content, current_lang="ko", ko_url="ko", en_url="en"), content)


if __name__ == "__main__":
    unittest.main()
