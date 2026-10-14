#!/usr/bin/env python3
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "blogger" / "theme-ai-oneplate.xml"


class BloggerThemeTests(unittest.TestCase):
    def test_theme_is_well_formed_and_declares_blogger_namespaces(self):
        tree = ET.parse(THEME)
        root = tree.getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/1999/xhtml}html")
        self.assertEqual(root.attrib["{http://www.google.com/2005/gml/b}version"], "2")

    def test_theme_contains_pages_shell_and_blogger_post_data(self):
        text = THEME.read_text(encoding="utf-8")
        for marker in (
            "site-header",
            "series-page-layout",
            "theme-ai-oneplate",
            "id='Blog1'",
            "type='Blog'",
            "b:includable id='main' var='top'",
            "b:includable id='post' var='post'",
            "data:post.body",
            "search/label/today-ai-bite",
            "search/label/weekly-ai",
            "search/label/easy-ai",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("{{", text)
        self.assertNotIn("{%", text)
    def test_theme_is_self_hosted_from_pages_runtime_dependencies(self):
        text = THEME.read_text(encoding="utf-8")
        self.assertNotIn("oneplate-ai.github.io", text)
        self.assertNotIn("@import", text)
        self.assertIn("all-head-content", text)


if __name__ == "__main__":
    unittest.main()
