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
            "labels": ["today-ai-bite-ko"],
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

    def test_identical_live_post_with_combined_label_is_a_noop_and_will_not_be_duplicated(self):
        module = self._module()
        source = {
            "key": ("AI basics", "en"),
            "title": "AI basics",
            "language": "en",
            "labels": ["easy-ai-en"],
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
            "labels": ["easy-ai-en"],
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
            "labels": ["easy-ai-en"],
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
        source = {"key": ("AI basics", "en"), "title": "AI basics", "language": "en", "labels": ["easy-ai-en"], "content": "<p>approved</p>"}
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
            "labels": ["easy-ai-en"],
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

    def test_live_post_with_owner_style_and_entity_encoding_is_not_rewritten(self):
        module = self._module()
        key = ("이번 주 AI 한 접시", "ko")
        plain = (
            '<p class="post-kicker">이번 주 AI 한 접시</p>'
            '<h2>1. 모델을 확인합니다. <a class="source-badge" href="https://example.test/source">공식 발표</a></h2>'
            '<p>모델·비용을 함께 확인합니다.</p>'
        )
        styled = (
            '<h1 style="text-align: center;">이번 주 AI 한 접시</h1>'
            '<h2>1. 모델을 확인합니다. <a class="source-badge" href="https://example.test/source">'
            '<span style="font-size: xx-small;">공식 발표</span></a></h2>'
            '<p>&nbsp;모델·비용을 함께 확인합니다.</p>'
        )
        current_body = styled.replace("모델·비용", "모델&#183;비용")
        source = {
            "key": key,
            "title": key[0],
            "language": "ko",
            "labels": ["weekly-ai-ko"],
            "content": styled,
            "unformatted_content": plain,
        }
        current = {
            key: ("LIVE", {"id": "live-style", "title": key[0], "labels": ["weekly-ai-ko"], "content": current_body})
        }

        self.assertEqual(module.plan_release({key: source}, current), [])

    def test_label_only_live_update_preserves_existing_body_byte_for_byte(self):
        module = self._module()
        ko_key, en_key = ("한국어 제목", "ko"), ("English title", "en")
        ko_source_body = '<h1 style="text-align: center;">한국어 제목</h1><p>&nbsp;모델·비용을 확인합니다.</p>'
        ko_live_body = ko_source_body.replace("모델·비용", "모델&#183;비용")
        sources = {
            ko_key: {
                "key": ko_key, "title": ko_key[0], "language": "ko", "labels": ["weekly-ai-ko"],
                "content": ko_source_body,
                "unformatted_content": '<p class="post-kicker">한국어 제목</p><p>모델·비용을 확인합니다.</p>',
                "publish_date": "2000-01-01T00:00:00+00:00",
            },
            en_key: {
                "key": en_key, "title": en_key[0], "language": "en", "labels": ["weekly-ai-en"],
                "content": "<p>English body</p>", "publish_date": "2000-01-01T00:00:00+00:00",
            },
        }
        posts = {
            ko_key: {"id": "ko-live", "title": ko_key[0], "labels": ["weekly-ai-ko", "legacy"], "content": ko_live_body, "url": "https://example.test/ko"},
            en_key: {"id": "en-live", "title": en_key[0], "labels": ["weekly-ai-en"], "content": sources[en_key]["content"], "url": "https://example.test/en"},
        }
        current = {ko_key: ("LIVE", posts[ko_key]), en_key: ("LIVE", posts[en_key])}
        plan = module.plan_release(sources, current)
        self.assertEqual(len(plan), 1)
        self.assertTrue(plan[0].get("preserve_content"))
        writes = []

        class Client:
            def update_post(self, post_id, payload):
                writes.append((post_id, dict(payload)))
                post = next(item for item in posts.values() if item["id"] == post_id)
                post.update(payload)
                return post

        release = module.ReleaseClient(Client())
        release.current = lambda *_args: current
        release.apply(sources, plan)
        self.assertEqual(writes[0][0], "ko-live")
        self.assertEqual(writes[0][1]["content"], ko_live_body)

        stale_posts = {
            ko_key: dict(posts[ko_key], labels=["weekly-ai-ko", "legacy"], content="<p>changed after plan</p>"),
            en_key: dict(posts[en_key]),
        }
        stale_current = {ko_key: ("LIVE", stale_posts[ko_key]), en_key: ("LIVE", stale_posts[en_key])}
        release.current = lambda *_args: stale_current
        writes_before_stale_plan = len(writes)
        with self.assertRaisesRegex(module.BloggerReleaseError, "기존 LIVE 글 갱신이 부분 완료"):
            release.apply(sources, plan)
        self.assertEqual(len(writes), writes_before_stale_plan)

    def test_live_readback_accepts_owner_style_when_text_and_links_match(self):
        module = self._module()
        key = ("이번 주 AI 한 접시", "ko")
        expected = (
            '<h1 style="text-align: center;">이번 주 AI 한 접시</h1>'
            '<h2>1. 항목. <a class="source-badge" href="https://example.test/source">'
            '<span style="font-size: xx-small;">공식 발표</span></a></h2>'
            '<p>&nbsp;내용·표현을 확인합니다.</p>'
        )
        current_body = expected.replace("내용·표현", "내용&#183;표현")
        source = {
            "key": key,
            "title": key[0],
            "language": "ko",
            "labels": ["weekly-ai-ko"],
            "content": expected,
            "unformatted_content": (
                '<p class="post-kicker">이번 주 AI 한 접시</p>'
                '<h2>1. 항목. <a class="source-badge" href="https://example.test/source">공식 발표</a></h2>'
                '<p>내용·표현을 확인합니다.</p>'
            ),
        }
        current = {
            key: ("LIVE", {"id": "live-style", "title": key[0], "labels": ["weekly-ai-ko"], "content": current_body})
        }
        release = object.__new__(module.ReleaseClient)
        release.current = lambda _keys: current

        try:
            verified = release.verify({key: source})
        except module.BloggerReleaseError as error:
            self.fail(f"owner-approved Blogger formatting was rejected: {error}")
        self.assertIs(verified[key][0], "LIVE")

    def test_unformatted_draft_is_updated_before_future_styled_publication(self):
        module = self._module()
        key = ("이번 주 AI 한 접시", "ko")
        plain = '<p class="post-kicker">이번 주 AI 한 접시</p><p>모델·비용을 확인합니다.</p>'
        styled = '<h1 style="text-align: center;">이번 주 AI 한 접시</h1><p>&nbsp;모델·비용을 확인합니다.</p>'
        source = {
            "key": key,
            "title": key[0],
            "language": "ko",
            "labels": ["weekly-ai-ko"],
            "content": styled,
            "unformatted_content": plain,
        }
        current = {
            key: ("DRAFT", {"id": "draft-style", "title": key[0], "labels": ["weekly-ai-ko"], "content": plain})
        }

        changes = module.plan_release({key: source}, current)

        self.assertEqual(changes[0]["action"], "update_draft_then_publish")

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
        self.assertEqual(sources[("승인된 제목", "ko")]["labels"], ["easy-ai-ko"])
        self.assertEqual(sources[("승인된 제목", "ko")]["content"], "<p>&nbsp;승인된 제목</p>")
        self.assertEqual(sources[("승인된 제목", "ko")]["unformatted_content"], "<p>승인된 제목</p>")
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
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": '<nav class="oneplate-language-switcher">old</nav><p>본문</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai-en"], "content": '<nav class="oneplate-language-switcher">old</nav><p>body</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertEqual(
            {tuple(item["key"]) for item in plan if item["action"] == "remove_language_switcher"},
            set(sources),
        )

    def test_new_bilingual_posts_do_not_plan_in_body_series_navigation(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>"},
        }
        plan = module.plan_release(sources, {})
        self.assertEqual(
            plan,
            [
                {"action": "create_draft_then_publish", "key": ("English title", "en")},
                {"action": "create_draft_then_publish", "key": ("한국어 제목", "ko")},
            ],
        )

    def test_legacy_series_navigation_does_not_produce_release_action(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai-en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        current[("한국어 제목", "ko")][1]["content"] += "<!-- oneplate-series-navigation:start --><section>old</section><!-- oneplate-series-navigation:end -->"
        current[("English title", "en")][1]["content"] += "<!-- oneplate-series-navigation:start --><section>old</section><!-- oneplate-series-navigation:end -->"
        self.assertEqual(module.plan_release(sources, current), [])

    def test_live_bilingual_pair_missing_navigation_is_not_added_to_plan(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": "<p>본문</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai-en"], "content": "<p>body</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertEqual(plan, [])


    def test_live_body_update_does_not_plan_redundant_switcher_write(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>approved ko</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>approved en</p>"},
        }
        en_switcher = module.inject_language_switcher("<p>approved en</p>", current_lang="en", ko_url="https://blog.example/ko", en_url="https://blog.example/en")
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": "<p>old ko</p>", "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai-en"], "content": en_switcher, "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
        }
        plan = module.plan_release(sources, current)
        self.assertIn({"action": "update_live", "id": "ko-id", "key": ("한국어 제목", "ko"), "navigation": ""}, plan)
        self.assertNotIn({"action": "remove_language_switcher", "id": "ko-id", "key": ("한국어 제목", "ko")}, plan)
        self.assertNotIn({"action": "remove_language_switcher", "id": "en-id", "key": ("English title", "en")}, plan)

    def test_post_publish_readback_failure_reports_published_ids(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
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
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
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
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
        release = object.__new__(module.ReleaseClient)
        release.create_fresh_draft = lambda _payload: {}
        changes = [{"action": "create_draft_then_publish", "key": source["key"]}]
        with self.assertRaisesRegex(module.BloggerReleaseError, "failed=.*create_draft_then_publish"):
            release.apply({source["key"]: source}, changes)

    def test_publish_failure_reports_attempted_draft_id(self):
        module = self._module()
        source = {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"}
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
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<h1 style=\"text-align: center;\">제목</h1><p>&nbsp;본문</p>", "unformatted_content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        class FakeClient:
            def __init__(self): self.published = []; self.updates = []
            def publish_draft(self, post_id, _when): self.published.append(post_id)
            def update_post(self, post_id, payload):
                self.updates.append((post_id, payload))
                raise module.BloggerApiError("live update failed")
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
        self.assertEqual(release.client.updates[0][1]["content"], "<p>본문</p>")

    def test_apply_rejects_unplanned_switcher_write_before_api_mutation(self):
        module = self._module()
        sources = {
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "title": "한국어 제목", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>본문</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
            ("English title", "en"): {"key": ("English title", "en"), "title": "English title", "language": "en", "labels": ["easy-ai-en"], "content": "<p>body</p>", "publish_date": "2000-01-01T00:00:00+00:00"},
        }
        current = {
            ("한국어 제목", "ko"): ("LIVE", {"id": "ko-id", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": '<nav class="oneplate-language-switcher">old</nav><p>본문</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/ko"}),
            ("English title", "en"): ("LIVE", {"id": "en-id", "title": "English title", "labels": ["easy-ai-en"], "content": '<nav class="oneplate-language-switcher">old</nav><p>body</p>', "published": "2026-10-14T10:00:00Z", "url": "https://blog.example/en"}),
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
            ("한국어 제목", "ko"): {"key": ("한국어 제목", "ko"), "language": "ko", "title": "한국어 제목", "labels": ["easy-ai-ko"], "content": "<p>본문</p>"},
            ("English title", "en"): {"key": ("English title", "en"), "language": "en", "title": "English title", "labels": ["easy-ai-en"], "content": "<p>body</p>"},
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

    def test_current_scope_ignores_unrelated_noncombined_drafts(self):
        module = self._module()
        allowed = {("Approved", "ko")}
        posts = [
            {"id": "staging", "title": "[staging]", "labels": ["ko", "media-staging"]},
            {"id": "old-draft", "title": "Older article", "labels": ["easy-ai", "ko"]},
            {"id": "collision", "title": "Approved", "labels": ["weekly-ai-ko"]},
        ]
        indexed = module.index_posts(posts, "DRAFT", allowed_keys=allowed)
        self.assertEqual(set(indexed), allowed)
        self.assertEqual(indexed[("Approved", "ko")][1]["id"], "collision")

    def test_cross_series_title_collision_is_rejected_not_overwritten(self):
        module = self._module()
        source = {"key": ("Shared title", "ko"), "title": "Shared title", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>approved</p>"}
        current = {source["key"]: ("LIVE", {"id": "other-series", "title": "Shared title", "labels": ["weekly-ai-ko"], "content": "<p>other</p>"})}
        with self.assertRaises(module.BloggerReleaseError):
            module.plan_release({source["key"]: source}, current)

    def test_scheduled_title_collision_blocks_release(self):
        module = self._module()
        source = {"key": ("Shared title", "ko"), "title": "Shared title", "language": "ko", "labels": ["easy-ai-ko"], "content": "<p>approved</p>"}
        current = {source["key"]: ("SCHEDULED", {"id": "scheduled", "title": "Shared title", "labels": ["easy-ai-ko"], "content": "<p>old</p>"})}
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
