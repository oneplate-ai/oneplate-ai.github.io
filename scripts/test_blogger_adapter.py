#!/usr/bin/env python3
import unittest
from collections.abc import Callable
from typing import cast

import blogger_adapter

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

    def test_future_blogger_style_formats_kicker_badges_and_prose_idempotently(self):
        candidate = getattr(blogger_adapter, "format_blogger_publication_content", None)
        self.assertTrue(callable(candidate), "Blogger publication style formatter is missing")
        formatter = cast(Callable[[str], str], candidate)
        source = (
            '<p class="post-kicker">이번 주 AI 한 접시</p>\n'
            '<p class="summary"><strong>요약.</strong><br />다음 문장.</p>\n'
            '<h2>첫 항목. <a class="source-badge" href="https://example.test/source">공식 자료</a></h2>\n'
            '<p class="multi-sentence">첫 문장.<br />둘째 문장.</p>\n'
            '<p class="post-verification">검증 안내.</p>\n'
            '<p class="verification-note">확인 문장.<br />다음 확인 문장.</p>\n'
            '<div class="nested"><p>중첩된 정상 문단</p></div>\n'
            '<p class="spacer"><br /></p>\n'
            '<ul><li>출처 목록</li></ul>'
        )

        styled = formatter(source)

        self.assertIn('<h1 style="text-align: center;">이번 주 AI 한 접시</h1>', styled)
        self.assertIn('<p class="summary">&nbsp;<strong>요약.</strong><br />다음 문장.</p>', styled)
        self.assertIn(
            '<a class="source-badge" href="https://example.test/source">'
            '<span style="font-size: xx-small;">공식 자료</span></a>',
            styled,
        )
        self.assertIn('<p class="multi-sentence">&nbsp;첫 문장.<br />&nbsp;둘째 문장.</p>', styled)
        self.assertIn('<p class="post-verification">&nbsp;검증 안내.</p>', styled)
        self.assertIn('<p class="verification-note">&nbsp;확인 문장.<br />&nbsp;다음 확인 문장.</p>', styled)
        self.assertIn('<div class="nested"><p>&nbsp;중첩된 정상 문단</p></div>', styled)
        self.assertIn('<p class="spacer"><br /></p>', styled)
        self.assertIn('<ul><li>출처 목록</li></ul>', styled)
        self.assertEqual(formatter(styled), styled)

    def test_formatter_fails_closed_on_comments_raw_blocks_and_malformed_paragraphs(self):
        candidate = getattr(blogger_adapter, "format_blogger_publication_content", None)
        self.assertTrue(callable(candidate), "Blogger publication style formatter is missing")
        formatter = cast(Callable[[str], str], candidate)
        unsupported = (
            '<!-- <p class="post-kicker">fake</p> -->',
            '<!-- <p>unclosed comment',
            '<!-- <p>fake</p>',
            '<script>const html = "<p>fake</p>";</script><p>본문</p>',
            '<textarea><p>텍스트 안의 태그</p></textarea><p>본문</p>',
            '<iframe><p>대체 콘텐츠 안의 문단</p></iframe><p>본문</p>',
            '<pre><p>코드 안의 태그</p></pre><p>본문</p>',
            '<p><p>중첩 문단</p></p>',
            '<p>닫히지 않은 문단',
            '<div><p>본문</div></p>',
            '<p title="앞 > 뒤">본문</p>',
            '<p title="닫히지 않은 속성>본문</p>',
        )

        for content in unsupported:
            with self.subTest(content=content), self.assertRaises(ValueError):
                formatter(content)

    def test_content_structure_signature_preserves_link_position_media_and_block_types(self):
        candidate = getattr(blogger_adapter, "content_structure_signature", None)
        self.assertTrue(callable(candidate), "structure-preserving HTML signature is missing")
        signature = cast(Callable[[str], object], candidate)
        linked_first = '<p><a href="https://example.test">더 보기</a> 더 보기</p>'
        linked_second = '<p>더 보기 <a href="https://example.test">더 보기</a></p>'
        video_one = '<p>영상</p><iframe src="https://example.test/one"></iframe>'
        video_two = '<p>영상</p><iframe src="https://example.test/two"></iframe>'
        image_one = '<img src="https://example.test/one.png" alt="그림" />'
        image_two = '<img src="https://example.test/two.png" alt="그림" />'

        self.assertNotEqual(signature(linked_first), signature(linked_second))
        self.assertNotEqual(signature(video_one), signature(video_two))
        self.assertNotEqual(signature(image_one), signature(image_two))
        self.assertNotEqual(signature('<h2>제목</h2>'), signature('<p>제목</p>'))
        self.assertEqual(signature('<p>비용·모델</p>'), signature('<p>비용&#183;모델</p>'))

        malformed = (
            '<p>x</p><p>',
            '<div><span>x</div></span>',
            '<p title="닫히지 않은 속성>본문</p>',
            '<!-- <p>미완성 주석</p>',
        )
        for content in malformed:
            with self.subTest(content=content), self.assertRaises(ValueError):
                signature(content)

    def test_legacy_injection_is_marker_free_noop(self):
        content = ' \n<p>본문입니다.</p>\n '
        self.assertEqual(inject_language_switcher(content, current_lang="ko", ko_url="ko", en_url="en"), content)


if __name__ == "__main__":
    unittest.main()
