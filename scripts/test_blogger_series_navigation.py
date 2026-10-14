#!/usr/bin/env python3
import unittest

from blogger_series_navigation import (
    NAV_END,
    NAV_START,
    apply_image_url_map,
    language_and_series,
    language_pair_urls,
    plan_changes,
    render_series_navigation,
    replace_navigation,
)


class BloggerSeriesNavigationTests(unittest.TestCase):
    def setUp(self):
        self.posts = [
            {
                "id": "newer",
                "title": "새 글",
                "url": "https://example.test/newer",
                "published": "2026-10-12T00:00:00+09:00",
                "labels": ["ko", "today-ai-bite"],
            },
            {
                "id": "older",
                "title": "이전 글",
                "url": "https://example.test/older",
                "published": "2026-10-09T00:00:00+09:00",
                "labels": ["ko", "today-ai-bite"],
            },
            {
                "id": "english",
                "title": "English post",
                "url": "https://example.test/english",
                "published": "2026-10-12T00:00:00+09:00",
                "labels": ["en", "today-ai-bite"],
            },
        ]

    def test_identifies_one_language_and_one_series(self):
        self.assertEqual(language_and_series(self.posts[0]), ("ko", "today-ai-bite"))
        with self.assertRaises(ValueError):
            language_and_series({"labels": ["ko", "en", "today-ai-bite"]})

    def test_renders_only_the_same_language_series_and_marks_current(self):
        markup = render_series_navigation(self.posts[0], self.posts)
        self.assertIn(NAV_START, markup)
        self.assertIn("오늘 AI 한입 글 목록", markup)
        self.assertIn('aria-current="page"', markup)
        self.assertIn("새 글", markup)
        self.assertIn("이전 글", markup)
        self.assertNotIn("English post", markup)

    def test_replace_navigation_is_idempotent_and_preserves_body(self):
        first = replace_navigation("<p>본문</p>", NAV_START + "<section>목록 A</section>" + NAV_END)
        second = replace_navigation(first, NAV_START + "<section>목록 B</section>" + NAV_END)
        self.assertEqual(second.count(NAV_START), 1)
        self.assertEqual(second.count(NAV_END), 1)
        self.assertIn("목록 B", second)
        self.assertNotIn("목록 A", second)
        self.assertIn("<p>본문</p>", second)

    def test_changes_only_exact_old_image_urls(self):
        old = "https://oneplate-ai.github.io/assets/images/old.svg"
        new = "https://blogger.googleusercontent.com/img/new.png"
        updated, changed = apply_image_url_map(
            f'<p><img src="{old}"/></p><a href="{old}">source</a>', {old: new}
        )
        self.assertTrue(changed)
        self.assertIn(f'src="{new}"', updated)
        self.assertIn(f'href="{old}"', updated)

    def test_pairs_same_series_and_publish_instant_for_counterpart_switchers(self):
        korean = {"id": "ko", "title": "한국어", "url": "https://blog.example/ko", "published": "2026-10-12T19:00:00+09:00", "labels": ["ko", "today-ai-bite"], "content": "<p>본문</p>"}
        english = {"id": "en", "title": "English", "url": "https://blog.example/en", "published": "2026-10-12T19:00:00+09:00", "labels": ["en", "today-ai-bite"], "content": "<p>body</p>"}
        pairs = language_pair_urls([korean, english])
        self.assertEqual(pairs["ko"], ("https://blog.example/ko", "https://blog.example/en"))
        post_changes, _ = plan_changes([korean, english], [], {})
        changed = {post["id"]: content for post, content in post_changes}
        self.assertIn('href="https://blog.example/en"', changed["ko"])
        self.assertIn('href="https://blog.example/ko"', changed["en"])
        self.assertIn(NAV_START, changed["ko"])
    def test_rejects_an_unpaired_language_post(self):
        korean = {"id": "ko", "title": "한국어", "url": "https://blog.example/ko", "published": "2026-10-12T19:00:00+09:00", "labels": ["ko", "today-ai-bite"], "content": "<p>본문</p>"}
        with self.assertRaisesRegex(ValueError, "불완전"):
            language_pair_urls([korean])


if __name__ == "__main__":
    unittest.main()
