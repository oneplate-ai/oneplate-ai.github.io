#!/usr/bin/env python3
import hashlib
import importlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class BloggerReleaseTests(unittest.TestCase):
    def _module(self):
        try:
            return importlib.import_module("blogger_release")
        except ModuleNotFoundError as error:
            self.fail(f"Blogger release automation is missing: {error}")

    def test_draft_is_updated_then_promoted_as_one_release_item(self):
        module = self._module()
        source = {
            "key": ("2026년 10월 15일", "ko"),
            "title": "2026년 10월 15일",
            "language": "ko",
            "labels": ["ko", "today-ai-bite"],
            "content": "<p>approved body</p>",
            "publish_date": "2026-10-15T19:00:00+09:00",
        }
        current = {
            source["key"]: ("DRAFT", {"id": "draft-1", "title": source["title"], "labels": source["labels"], "content": "<p>old</p>"})
        }
        self.assertEqual(
            module.plan_release({source["key"]: source}, current),
            [{"action": "update_draft_then_publish", "id": "draft-1", "key": source["key"]}],
        )

    def test_identical_live_post_is_a_noop_and_will_not_be_duplicated(self):
        module = self._module()
        source = {
            "key": ("AI basics", "en"),
            "title": "AI basics",
            "language": "en",
            "labels": ["easy-ai", "en"],
            "content": "<p>approved body</p>",
            "publish_date": "2026-10-15T19:00:00+09:00",
        }
        current = {
            source["key"]: ("LIVE", {"id": "live-1", "title": source["title"], "labels": source["labels"], "content": source["content"]})
        }
        self.assertEqual(module.plan_release({source["key"]: source}, current), [])

    def test_live_post_navigation_block_is_ignored_for_content_parity(self):
        module = self._module()
        source = {
            "key": ("AI basics", "en"),
            "title": "AI basics",
            "language": "en",
            "labels": ["easy-ai", "en"],
            "content": "<p>approved body</p>",
            "publish_date": "2026-10-15T19:00:00+09:00",
        }
        navigation = "<!-- oneplate-series-navigation:start --><section>navigation</section><!-- oneplate-series-navigation:end -->"
        current = {
            source["key"]: ("LIVE", {"id": "live-1", "title": source["title"], "labels": source["labels"], "content": navigation + source["content"]})
        }
        self.assertEqual(module.plan_release({source["key"]: source}, current), [])

    def test_missing_blogger_image_mapping_blocks_release(self):
        module = self._module()
        if not hasattr(module, "ensure_image_mapping"):
            self.fail("Blogger image mapping guard is missing")
        with self.assertRaises(module.BloggerReleaseError):
            module.ensure_image_mapping(
                '<img src="https://oneplate-ai.github.io/assets/images/new-illustration.svg">',
                {},
            )

    def test_live_post_with_different_approved_body_is_updated_not_recreated(self):
        module = self._module()
        source = {
            "key": ("AI basics", "en"),
            "title": "AI basics",
            "language": "en",
            "labels": ["easy-ai", "en"],
            "content": "<p>approved body</p>",
            "publish_date": "2026-10-15T19:00:00+09:00",
        }
        current = {
            source["key"]: ("LIVE", {"id": "live-1", "title": source["title"], "labels": source["labels"], "content": "<p>old</p>"})
        }
        self.assertEqual(
            module.plan_release({source["key"]: source}, current),
            [{"action": "update_live", "id": "live-1", "key": source["key"], "navigation": ""}],
        )

    def test_live_update_preserves_existing_series_navigation(self):
        module = self._module()
        source = {"key": ("AI basics", "en"), "title": "AI basics", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>approved</p>"}
        navigation = "<!-- oneplate-series-navigation:start --><section>navigation</section><!-- oneplate-series-navigation:end -->"
        current = {source["key"]: ("LIVE", {"id": "live-1", "title": source["title"], "labels": source["labels"], "content": "<p>old</p>" + navigation})}
        plan = module.plan_release({source["key"]: source}, current)
        self.assertEqual(plan[0]["navigation"], navigation)

    def test_live_language_switcher_is_ignored_for_content_parity(self):
        module = self._module()
        source = {
            "key": ("AI basics", "en"),
            "title": "AI basics",
            "language": "en",
            "labels": ["easy-ai", "en"],
            "content": "<p>approved body</p>",
            "publish_date": "2026-10-15T19:00:00+09:00",
        }
        switcher = (
            '<nav class="oneplate-language-switcher" aria-label="언어 선택">'
            '<a href="https://example.test/ko">한국어</a>'
            '<span aria-hidden="true"> · </span>'
            '<a href="https://example.test/en" aria-current="page">English</a>'
            '</nav>'
        )
        current = {
            source["key"]: ("LIVE", {"id": "live-1", "title": source["title"], "labels": source["labels"], "content": switcher + source["content"]})
        }
        self.assertEqual(module.plan_release({source["key"]: source}, current), [])

    def test_pages_readback_requires_each_public_title(self):
        module = self._module()
        approval = {"drafts": [
            {"lang": "ko", "permalink": "/posts/ko.html", "title": "한국어 제목"},
            {"lang": "en", "permalink": "/en/posts/en.html", "title": "English title"},
        ]}
        seen = []
        def fetch(url):
            seen.append(url)
            return '<html><head><title>ok</title></head><body>한국어 제목 English title</body></html>'
        module.verify_pages_public_pair(approval, "https://pages.example", fetch=fetch)
        self.assertEqual(seen, [
            "https://pages.example/posts/ko.html",
            "https://pages.example/en/posts/en.html",
        ])

    def test_release_sources_are_built_directly_from_approved_drafts(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft_dir = root / "drafts"
            draft_dir.mkdir()
            drafts = []
            for lang, title, slug in (("ko", "승인된 제목", "approved-ko"), ("en", "Approved title", "approved-en")):
                permalink = f"/{'en/' if lang == 'en' else ''}posts/{slug}.html"
                text = (
                    "---\n"
                    f'title: "{title}"\n'
                    f"series: easy-ai\nlang: {lang}\ndate: 2026-10-15\n"
                    "translation_key: easy-ai-test\n"
                    f'permalink: "{permalink}"\n'
                    "published: false\ndraft: true\nnoindex: true\n---\n"
                    f"<p>{title}</p>\n"
                )
                path = draft_dir / f"{slug}.md"
                path.write_text(text, encoding="utf-8")
                drafts.append({
                    "lang": lang,
                    "exact_draft_path": f"drafts/{path.name}",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "title": title,
                    "date": "2026-10-15",
                    "translation_key": "easy-ai-test",
                    "permalink": permalink,
                })
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps({
                "schema_version": 2,
                "publication_unit": "easy-ai-2026-10-15-bilingual",
                "series": "easy-ai",
                "approved_at": "2026-10-14T12:00:00+09:00",
                "scheduled_publish_at": "2026-10-15T19:00:00+09:00",
                "approval_text_literal": "정확한 한영 글 발행을 승인합니다.",
                "blogger_image_map_sha256": hashlib.sha256(b"{}").hexdigest(),
                "drafts": drafts,
            }, ensure_ascii=False), encoding="utf-8")
            image_map = root / "images.json"
            image_map.write_text("{}", encoding="utf-8")

            sources = module.release_sources(root, approval_path, image_map)

        self.assertEqual(set(sources), {("승인된 제목", "ko"), ("Approved title", "en")})
        self.assertEqual(sources[("승인된 제목", "ko")]["labels"], ["easy-ai", "ko"])
        self.assertIn("<p>승인된 제목</p>", sources[("승인된 제목", "ko")]["content"])
        self.assertEqual(sources[("승인된 제목", "ko")]["source_path"], "drafts/approved-ko.md")

    def test_draft_relative_image_is_mapped_before_blogger_payload(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft_dir = root / "drafts"
            draft_dir.mkdir()
            drafts = []
            for lang, title, slug in (("ko", "이미지 제목", "image-ko"), ("en", "Image title", "image-en")):
                permalink = f"/{'en/' if lang == 'en' else ''}posts/{slug}.html"
                path = draft_dir / f"{slug}.md"
                path.write_text(
                    "---\n" + f'title: "{title}"\nseries: easy-ai\nlang: {lang}\ndate: 2026-10-15\n'
                    + "translation_key: image-test\n" + f'permalink: "{permalink}"\n'
                    + "published: false\ndraft: true\nnoindex: true\n---\n"
                    + '<img src="{{ \'/assets/images/lesson.svg\' | relative_url }}">\n',
                    encoding="utf-8",
                )
                drafts.append({"lang": lang, "exact_draft_path": f"drafts/{path.name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "title": title, "date": "2026-10-15", "translation_key": "image-test", "permalink": permalink})
            image_map = root / "images.json"
            image_map_data = {"lesson.svg": "https://lh3.googleusercontent.com/lesson.png"}
            image_map.write_text(json.dumps(image_map_data), encoding="utf-8")
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps({"schema_version": 2, "publication_unit": "easy-ai-2026-10-15-bilingual", "series": "easy-ai", "approved_at": "2026-10-14T12:00:00+09:00", "scheduled_publish_at": "2026-10-15T19:00:00+09:00", "approval_text_literal": "승인합니다.", "blogger_image_map_sha256": hashlib.sha256(image_map.read_bytes()).hexdigest(), "drafts": drafts}, ensure_ascii=False), encoding="utf-8")
            sources = module.release_sources(root, approval_path, image_map)
        self.assertIn("https://lh3.googleusercontent.com/lesson.png", sources[("이미지 제목", "ko")]["content"])
        self.assertNotIn("oneplate-ai.github.io", sources[("이미지 제목", "ko")]["content"])

    def test_public_copy_does_not_rewrite_ordinary_body_or_code_text(self):
        module = self._module()
        source = (
            "---\n"
            "published: false\ndraft: true\nnoindex: true\ntitle: Example\n---\n"
            "<p>This draft is ordinary approved prose.</p>\n"
            "<pre>This draft is code text.</pre>\n"
            '<p class="post-verification">This draft is a verification note.</p>\n'
        )
        public = module.public_copy_from_draft(source)
        self.assertIn("published: true", public)
        self.assertNotIn("draft: true", public)
        self.assertNotIn("noindex: true", public)
        self.assertIn("This draft is ordinary approved prose.", public)
        self.assertIn("This draft is code text.", public)
        self.assertIn("This post is a verification note.", public)

    def test_image_map_rejects_pages_url_and_requires_matching_approval_digest(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "images.json"
            path.write_text(json.dumps({"lesson.svg": "https://oneplate-ai.github.io/assets/images/lesson.svg"}), encoding="utf-8")
            with self.assertRaises(module.BloggerReleaseError):
                module._image_urls(path)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(module.BloggerReleaseError):
                module.verify_image_map_approval({"blogger_image_map_sha256": "0" * 64}, path.read_bytes())

    def test_bilingual_switcher_removals_are_disclosed_in_the_plan(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": '<nav class="oneplate-language-switcher">old</nav><p>본문</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": '<nav class="oneplate-language-switcher">old</nav><p>body</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertEqual(
            {tuple(item["key"]) for item in plan if item["action"] == "remove_language_switcher"},
            set(sources),
        )

    def test_new_bilingual_posts_plan_only_series_navigation_after_publish(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>"},
        }
        plan = module.plan_release(sources, {})
        self.assertIn(
            {"action": "update_series_navigation_after_publish", "keys": sorted(sources)},
            plan,
        )

    def test_series_navigation_updates_render_new_live_pair(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        updates = module.series_navigation_updates(sources, current)
        self.assertEqual({update["id"] for update in updates}, {"ko-id", "en-id"})
        self.assertTrue(all("oneplate-series-navigation" in update["content"] for update in updates))

    def test_live_bilingual_pair_missing_navigation_is_disclosed_in_plan(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertEqual(
            {tuple(item["key"]) for item in plan if item["action"] == "update_series_navigation"},
            set(sources),
        )

    def test_apply_writes_only_planned_series_navigation_without_switchers(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        class FakeClient:
            def __init__(self): self.calls = []
            def update_post(self, post_id, payload):
                self.calls.append((post_id, payload))
                for state, post in current.values():
                    if state == "LIVE" and post["id"] == post_id:
                        post["content"] = payload["content"]
                        return {"content": payload["content"]}
                raise AssertionError(post_id)
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify = lambda _sources: current
        changes = [{"action": "update_series_navigation_after_publish", "keys": sorted(sources)}]
        result = release.apply(sources, changes)
        self.assertEqual(len(release.client.calls), 2)
        self.assertEqual(len(result["navigation_completed"]), 2)
        self.assertEqual(len(result["switcher_completed"]), 0)
        self.assertTrue(all("oneplate-series-navigation" in post["content"] for _, post in current.values()))
        self.assertTrue(all("oneplate-language-switcher" not in post["content"] for _, post in current.values()))

    def test_later_series_failure_reports_completed_navigation(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        class FakeClient:
            def __init__(self): self.calls = 0
            def update_post(self, post_id, payload):
                self.calls += 1
                if self.calls == 2:
                    raise module.BloggerApiError("switcher write failed")
                for state, post in current.values():
                    if state == "LIVE" and post["id"] == post_id:
                        post["content"] = payload["content"]
                        return {"content": payload["content"]}
                raise AssertionError(post_id)
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify = lambda _sources: current
        changes = [{"action": "update_series_navigation_after_publish", "keys": sorted(sources)}]
        with self.assertRaises(module.BloggerReleaseError) as caught:
            release.apply(sources, changes)
        self.assertIn("navigation_completed", str(caught.exception))
        self.assertIn("ko-id", str(caught.exception))
        self.assertIn("en-id", str(caught.exception))

    def test_live_body_update_does_not_plan_redundant_switcher_write(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>approved ko</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>approved en</p>"},
        }
        en_switcher = module.inject_language_switcher("<p>approved en</p>", current_lang="en", ko_url="https://blog.example/ko", en_url="https://blog.example/en")
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>old ko</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": en_switcher, "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertIn({"action": "update_live", "id": "ko-id", "key": ("한국어 제목", "ko"), "navigation": ""}, plan)
        self.assertNotIn({"action": "remove_language_switcher", "id": "ko-id", "key": ("한국어 제목", "ko")}, plan)
        self.assertNotIn({"action": "remove_language_switcher", "id": "en-id", "key": ("English title", "en")}, plan)

    def test_post_publish_readback_failure_reports_published_ids(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        class FakeClient:
            def __init__(self): self.published = []
            def publish_draft(self, post_id, _when): self.published.append(post_id)
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify_staged_draft = lambda *_args: None
        release.verify = lambda _sources: (_ for _ in ()).throw(module.BloggerReleaseError("read-back failed"))
        changes = [
            {"action": "publish_draft", "id": "ko-id", "key": ("한국어 제목", "ko")},
            {"action": "publish_draft", "id": "en-id", "key": ("English title", "en")},
        ]
        with self.assertRaisesRegex(module.BloggerReleaseError, "published=.*ko-id.*en-id"):
            release.apply(sources, changes)
        self.assertEqual(release.client.published, ["ko-id", "en-id"])

    def test_staged_draft_readback_failure_reports_prepared_ids(self):
        module = self._module()
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
        release = object.__new__(module.ReleaseClient)
        release.client = object()
        release.verify_staged_draft = lambda *_args: (_ for _ in ()).throw(module.BloggerReleaseError("staged read-back failed"))
        changes = [{"action": "update_draft_then_publish", "id": "draft-42", "key": source["key"]}]
        class UpdatingClient:
            def update_draft(self, *_args): pass
        release.client = UpdatingClient()
        with self.assertRaisesRegex(module.BloggerReleaseError, "prepared=.*draft-42"):
            release.apply({source["key"]: source}, changes)

    def test_create_draft_response_without_id_reports_failed_action(self):
        module = self._module()
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
        release = object.__new__(module.ReleaseClient)
        release.create_fresh_draft = lambda _payload: {}
        changes = [{"action": "create_draft_then_publish", "key": source["key"]}]
        with self.assertRaisesRegex(module.BloggerReleaseError, "failed=.*create_draft_then_publish"):
            release.apply({source["key"]: source}, changes)

    def test_publish_failure_reports_attempted_draft_id(self):
        module = self._module()
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
        class FakeClient:
            def publish_draft(self, _post_id, _when): raise module.BloggerApiError("ambiguous publish failure")
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify_staged_draft = lambda *_args: None
        changes = [{"action": "publish_draft", "id": "en-draft-id", "key": source["key"]}]
        with self.assertRaisesRegex(module.BloggerReleaseError, "failed=.*en-draft-id"):
            release.apply({source["key"]: source}, changes)

    def test_live_update_failure_reports_already_published_counterpart(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        class FakeClient:
            def __init__(self): self.published = []
            def publish_draft(self, post_id, _when): self.published.append(post_id)
            def update_post(self, _post_id, _payload): raise module.BloggerApiError("live update failed")
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify_staged_draft = lambda *_args: None
        changes = [
            {"action": "publish_draft", "id": "en-draft-id", "key": ("English title", "en")},
            {"action": "update_live", "id": "ko-live-id", "key": ("한국어 제목", "ko"), "navigation": ""},
        ]
        with self.assertRaisesRegex(module.BloggerReleaseError, "published=.*en-draft-id.*ko-live-id"):
            release.apply(sources, changes)
        self.assertEqual(release.client.published, ["en-draft-id"])

    def test_apply_rejects_unplanned_switcher_write_before_api_mutation(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        class FakeClient:
            def __init__(self): self.calls = []
            def update_post(self, *args): self.calls.append(args); return {}
        release = object.__new__(module.ReleaseClient)
        release.client = FakeClient()
        release.verify = lambda _sources: current
        with self.assertRaises(module.BloggerReleaseError):
            release.apply(sources, [])
        self.assertEqual(release.client.calls, [])

    def test_approval_gate_rejects_changed_draft_before_payload_creation(self):
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "drafts").mkdir()
            draft = root / "drafts" / "ko.md"
            draft.write_text("changed after approval", encoding="utf-8")
            approval = root / "approval.json"
            approval.write_text("{}", encoding="utf-8")
            image_map = root / "images.json"
            image_map.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(module.BloggerReleaseError, "승인 원본"):
                module.release_sources(root, approval, image_map)

    def test_pages_readback_is_optional_for_standalone_blogger_release(self):
        module = self._module()
        approval = {"drafts": [{"lang": "ko", "permalink": "/posts/ko.html", "title": "한국어 제목"}]}
        def unavailable_fetch(_url):
            raise AssertionError("standalone release must not fetch Pages")
        self.assertEqual(
            module.optional_pages_readback(approval, "https://pages.example", required=False, fetch=unavailable_fetch),
            [],
        )

    def test_language_switcher_removal_preserves_series_navigation(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "language": "ko", "title": "한국어 제목", "labels": ["easy-ai", "ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "language": "en", "title": "English title", "labels": ["easy-ai", "en"], "content": "<p>body</p>"},
        }
        navigation = "<!-- oneplate-series-navigation:start --><section>navigation</section><!-- oneplate-series-navigation:end -->"
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "url": "https://blog.example/ko", "content": '<nav class="oneplate-language-switcher">old</nav>' + navigation + "<p>본문</p>"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "url": "https://blog.example/en", "content": '<nav class="oneplate-language-switcher">old</nav>' + navigation + "<p>body</p>"}),
        }
        updates = module.language_switcher_updates(sources, current)
        self.assertEqual(len(updates), 2)
        self.assertNotIn("oneplate-language-switcher", updates[0]["content"])
        self.assertIn(navigation, updates[0]["content"])

    def test_cross_series_title_collision_is_rejected_not_overwritten(self):
        module = self._module()
        source = {"key": ("Shared title", "ko"), "title": "Shared title", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>approved</p>"}
        current = {source["key"]: ("LIVE", {"id": "other-series", "title": "Shared title", "labels": ["weekly-ai", "ko"], "content": "<p>other</p>"})}
        with self.assertRaises(module.BloggerReleaseError):
            module.plan_release({source["key"]: source}, current)

    def test_scheduled_title_collision_blocks_release(self):
        module = self._module()
        source = {"key": ("Shared title", "ko"), "title": "Shared title", "language": "ko", "labels": ["easy-ai", "ko"], "content": "<p>approved</p>"}
        current = {source["key"]: ("SCHEDULED", {"id": "scheduled", "title": "Shared title", "labels": ["easy-ai", "ko"], "content": "<p>old</p>"})}
        with self.assertRaises(module.BloggerReleaseError):
            module.plan_release({source["key"]: source}, current)

    def test_pages_asset_in_srcset_is_mapped_or_blocks_release(self):
        module = self._module()
        canonical = "https://oneplate-ai.github.io/assets/images/new.svg"
        mapped_url = "https://lh3.googleusercontent.com/new.png"
        for old in (
            canonical,
            "http://oneplate-ai.github.io/assets/images/new.svg",
            "https://oneplate-ai.github.io:443/assets/images/new.svg",
            "https://oneplate-ai.github.io./assets/images/new.svg",
            "//oneplate-ai.github.io/assets/images/new.svg",
        ):
            content = f'<img srcset="{old} 1x" src = "{old}">'
            with self.assertRaises(module.BloggerReleaseError):
                module.ensure_image_mapping(content, {})
            mapped = module.apply_image_mapping(content, {canonical: mapped_url})
            self.assertNotIn("oneplate-ai.github.io", mapped)
            self.assertIn(mapped_url, mapped)
        with self.assertRaises(module.BloggerReleaseError):
            module.ensure_image_mapping(
                '<img src="https://user@oneplate-ai.github.io/assets/images/new.svg">',
                {canonical: mapped_url},
            )
        with self.assertRaises(module.BloggerReleaseError):
            module.ensure_image_mapping(
                '<img src="https://oneplate-ai.github.io/legacy-image.png">',
                {canonical: mapped_url},
            )
        with self.assertRaises(module.BloggerReleaseError):
            module.ensure_image_mapping(
                '<img src="https://oneplate-ai&#46;github.io/assets/images/new.svg">',
                {canonical: mapped_url},
            )
        with tempfile.TemporaryDirectory() as temp:
            unsafe_map = Path(temp) / "unsafe-images.json"
            unsafe_map.write_text(json.dumps({"new.svg": 'https://lh3.googleusercontent.com/a.png" onerror="alert(1)'}), encoding="utf-8")
            with self.assertRaises(module.BloggerReleaseError):
                module._image_urls(unsafe_map)

    def test_future_blogger_publish_time_is_rejected_before_writes(self):
        module = self._module()
        with self.assertRaises(module.BloggerReleaseError):
            module.require_immediate_publish_time(
                "2026-10-15T19:00:00+09:00",
                now=datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc),
            )

    def test_fresh_draft_creation_does_not_reuse_title_only_helper(self):
        module = self._module()
        self.assertTrue(hasattr(module.ReleaseClient, "create_fresh_draft"))
        self.assertNotIn("create_draft(", module.ReleaseClient.create_fresh_draft.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
