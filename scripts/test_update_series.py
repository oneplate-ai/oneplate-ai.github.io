#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from update_series import discover_posts, update


POST_TEMPLATE = """<html><body><article><p class=\"post-kicker\">Weekly</p><h1>{title}</h1><p>{body}</p><aside class=\"series-archive\"><ol></ol></aside></article></body></html>"""
LANDING = """<html><body><article id=\"latest\"><p>old</p></article><aside class=\"series-archive\"><ol></ol></aside></body></html>"""


class UpdateSeriesTests(unittest.TestCase):
    def test_weekly_canonical_slug_accepts_historical_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "posts").mkdir()
            (root / "series" / "weekly-ai").mkdir(parents=True)
            post = root / "posts" / "2026-09-19-weekly-ai-oneplate.html"
            post.write_text(POST_TEMPLATE.format(title="Current", body="new"), encoding="utf-8")
            (root / "series" / "weekly-ai" / "index.html").write_text(LANDING, encoding="utf-8")

            posts = discover_posts(root, "weekly-ai")
            self.assertEqual([p.name for _, p, _, _ in posts], [post.name])
            update(root, "weekly-ai")

            self.assertIn("Current", (root / "series" / "weekly-ai" / "index.html").read_text(encoding="utf-8"))

    def test_exact_filename_series_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "posts").mkdir()
            post = root / "posts" / "2026-09-18-today-ai-bite.html"
            post.write_text(POST_TEMPLATE.format(title="Daily", body="new"), encoding="utf-8")
            posts = discover_posts(root, "today-ai-bite")
            self.assertEqual([p.name for _, p, _, _ in posts], [post.name])


if __name__ == "__main__":
    unittest.main()
