#!/usr/bin/env python3
import unittest

from blogger_full_parity_sync import (
    apply_blogger_image_urls,
    canonical_sources,
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


if __name__ == "__main__":
    unittest.main()
