#!/usr/bin/env python3
import unittest
from typing import Any

from blogger_full_parity_sync import (
    apply_blogger_image_urls,
    canonical_sources,
    plan as plan_sync,
    target_state,
)


class BloggerFullParitySyncTests(unittest.TestCase):
    def test_posts_win_over_duplicate_drafts_but_draft_only_article_is_kept(self):
        sources = canonical_sources(
            [
                {"key": ("Public", "ko"), "path": "_posts/public.md", "state": "LIVE"},
                {"key": ("Public", "ko"), "path": "drafts/old-public.md", "state": "DRAFT"},
                {"key": ("Draft only", "en"), "path": "drafts/draft.md", "state": "DRAFT"},
            ]
        )
        self.assertEqual(sources[("Public", "ko")]["path"], "_posts/public.md")
        self.assertEqual(sources[("Draft only", "en")]["path"], "drafts/draft.md")
        self.assertEqual(target_state(sources[("Public", "ko")]), "LIVE")
        self.assertEqual(target_state(sources[("Draft only", "en")]), "DRAFT")

    def test_replaces_only_approved_blogger_image_paths(self):
        old = "https://oneplate-ai.github.io/assets/images/example.svg"
        new = "https://blogger.googleusercontent.com/img/example.png"
        content = f'<img src="{old}"><a href="{old}">source</a>'
        result = apply_blogger_image_urls(content, {old: new})
        self.assertIn(f'src="{new}"', result)
        self.assertIn(f'href="{old}"', result)

    def test_live_source_style_does_not_retroactively_rewrite_plain_or_owner_styled_posts(self):
        key: tuple[str, str] = ("Weekly", "ko")
        plain = '<p class="post-kicker">Weekly</p><p>Text.</p>'
        styled = '<h1 style="text-align: center;">Weekly</h1><p>&nbsp;Text.</p>'
        source = {
            "key": key,
            "path": "_posts/weekly.md",
            "state": "LIVE",
            "labels": ["weekly-ai-ko"],
            "content": styled,
            "unformatted_content": plain,
            "publish_date": "2026-10-24T12:00:00+09:00",
        }

        for body in (plain, styled):
            current: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {
                key: ("LIVE", {
                    "id": "live-1",
                    "title": key[0],
                    "labels": source["labels"],
                    "content": body,
                })
            }
            with self.subTest(body=body):
                self.assertEqual(plan_sync({key: source}, current), [])


if __name__ == "__main__":
    unittest.main()
