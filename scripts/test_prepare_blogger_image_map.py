#!/usr/bin/env python3
import importlib
import tempfile
import unittest
from pathlib import Path


class PrepareBloggerImageMapTests(unittest.TestCase):
    def _module(self):
        return importlib.import_module("prepare_blogger_image_map")

    def _draft(self, root: Path, name: str, lang: str, body: str) -> Path:
        path = root / "drafts" / name
        path.parent.mkdir(exist_ok=True)
        permalink = f"/{'en/' if lang == 'en' else ''}posts/example.html"
        path.write_text(
            "---\n"
            f"title: Example {lang}\nseries: today-ai-bite\nlang: {lang}\ndate: 2026-10-15\n"
            "translation_key: today-ai-bite-2026-10-15\n"
            f"permalink: {permalink}\n"
            "published: false\ndraft: true\nnoindex: true\n---\n"
            + body,
            encoding="utf-8",
        )
        return path

    def test_writes_empty_map_for_a_text_only_bilingual_pair(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ko = self._draft(root, "ko.md", "ko", "<p>본문</p>\n")
            en = self._draft(root, "en.md", "en", "<p>body</p>\n")
            output = root / "approval" / "images.json"
            result = module.prepare_image_map(root, ko, en, output)
            self.assertEqual(result["status"], "ready_no_images")
            self.assertEqual(result["required_image_keys"], [])
            self.assertEqual(output.read_text(encoding="utf-8"), "{}\n")

    def test_reports_required_mapping_without_creating_an_unsafe_map(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ko = self._draft(root, "ko.md", "ko", '<img src="{{ \'/assets/images/lesson.svg\' | relative_url }}">\n')
            en = self._draft(root, "en.md", "en", '<img src="{{ \'/assets/images/lesson.svg\' | relative_url }}">\n')
            output = root / "approval" / "images.json"
            result = module.prepare_image_map(root, ko, en, output)
            self.assertEqual(result["status"], "mapping_required")
            self.assertEqual(result["required_image_keys"], ["lesson.svg"])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
