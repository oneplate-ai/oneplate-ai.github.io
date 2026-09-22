#!/usr/bin/env python3
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from publication_gate import GateError, validate_approval, validate_scope, weekly_inventory


DRAFT = """---
title: {title}
series: {series}
lang: {lang}
date: {date}
translation_key: {key}
permalink: {permalink}
published: false
draft: true
noindex: true
---
본문입니다.
"""


class PublicationGateTests(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "drafts").mkdir()
        (root / "_posts").mkdir()
        return tmp, root

    def write_pair(self, root, series="today-ai-bite", day="2026-09-21"):
        key = f"{series}-{day}"
        files = []
        for lang, prefix, title in [
            ("ko", "/posts/", "2026년 9월 21일"),
            ("en", "/en/posts/", "September 21, 2026"),
        ]:
            name = f"{day}-{series}.md"
            if lang == "en":
                name = f"{day}-{series}-en.md"
            path = root / "drafts" / name
            path.write_text(DRAFT.format(
                title=title,
                series=series,
                lang=lang,
                date=day,
                key=key,
                permalink=f"{prefix}{day}-{series}.html",
            ), encoding="utf-8")
            files.append((lang, path, title, f"{prefix}{day}-{series}.html"))
        return files

    def approval(self, root, series="today-ai-bite", day="2026-09-21"):
        files = self.write_pair(root, series, day)
        key = f"{series}-{day}"
        record = {
            "schema_version": 2,
            "publication_unit": f"{series}-{day}-bilingual",
            "series": series,
            "approved_at": "2026-09-21T12:00:00+09:00",
            "scheduled_publish_at": "2026-09-21T19:00:00+09:00",
            "approval_text_literal": "정확한 한영 문안 발행을 승인합니다.",
            "drafts": [],
        }
        for lang, path, title, permalink in files:
            record["drafts"].append({
                "lang": lang,
                "exact_draft_path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "title": title,
                "date": day,
                "translation_key": key,
                "permalink": permalink,
            })
        approval = root / "approval.json"
        approval.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return approval

    def test_valid_bilingual_approval(self):
        tmp, root = self.make_root()
        with tmp:
            approval = self.approval(root)
            data = validate_approval(approval, root, "today-ai-bite", "2026-09-21")
            self.assertEqual(data["schema_version"], 2)

    def test_legacy_key_names_are_rejected(self):
        tmp, root = self.make_root()
        with tmp:
            approval = self.approval(root)
            data = json.loads(approval.read_text(encoding="utf-8"))
            data["drafts"][0]["language"] = data["drafts"][0].pop("lang")
            data["drafts"][0]["path"] = data["drafts"][0].pop("exact_draft_path")
            approval.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(GateError):
                validate_approval(approval, root, "today-ai-bite", "2026-09-21")

    def test_hash_mismatch_is_rejected(self):
        tmp, root = self.make_root()
        with tmp:
            approval = self.approval(root)
            draft = next((root / "drafts").glob("*.md"))
            draft.write_text(draft.read_text(encoding="utf-8") + "변경\n", encoding="utf-8")
            with self.assertRaisesRegex(GateError, "SHA-256 불일치"):
                validate_approval(approval, root, "today-ai-bite", "2026-09-21")

    def test_scope_rejects_mixed_series(self):
        with self.assertRaisesRegex(GateError, "범위 밖"):
            validate_scope([
                "_posts/2026-09-21-today-ai-bite.md",
                "_posts/2026-09-21-today-ai-bite-en.md",
                "_posts/2026-09-19-weekly-ai-oneplate.md",
            ], "today-ai-bite", "2026-09-21")

    def test_scope_accepts_exact_bilingual_pair(self):
        paths = validate_scope([
            "_posts/2026-09-21-today-ai-bite.md",
            "_posts/2026-09-21-today-ai-bite-en.md",
        ], "today-ai-bite", "2026-09-21")
        self.assertEqual(len(paths), 2)

    def test_weekly_inventory_excludes_unpublished_and_english(self):
        tmp, root = self.make_root()
        with tmp:
            for name, lang, published in [
                ("2026-09-18-today-ai-bite.md", "ko", "true"),
                ("2026-09-18-today-ai-bite-en.md", "en", "true"),
                ("2026-09-16-today-ai-bite.md", "ko", "false"),
            ]:
                (root / "_posts" / name).write_text(
                    DRAFT.format(title=name, series="today-ai-bite", lang=lang,
                                 date=name[:10], key=name, permalink="/posts/x")
                    .replace("published: false", f"published: {published}"),
                    encoding="utf-8",
                )
            items = weekly_inventory(root, "2026-09-13", "2026-09-19")
            self.assertEqual([item["date"] for item in items], ["2026-09-18"])


if __name__ == "__main__":
    unittest.main()
