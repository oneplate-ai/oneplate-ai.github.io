import unittest

from blogger_adapter import inject_language_switcher


class LanguageCleanupTests(unittest.TestCase):
    def live_pair(self):
        import blogger_release as release
        sources, current = {}, {}
        for lang in ('ko', 'en'):
            key = (lang, lang)
            source = dict(key=key, title=lang, language=lang, labels=['easy-ai', lang],
                          content=' \n<p>approved ' + lang + '</p>\n ', publish_date='2000-01-01T00:00:00Z')
            sources[key] = source
            post = dict(id=lang, title=lang, labels=source['labels'], url='https://example.test/' + lang,
                        published='2000-01-01T00:00:00Z', content=source['content'])
            current[key] = ('LIVE', post)
        for _, post in current.values():
            post['content'] = release.replace_navigation(post['content'], release.render_series_navigation(post, [p for _, p in current.values()]))
            post['content'] = '<nav class="oneplate-language-switcher">old</nav>' + post['content']
        return sources, current

    def test_live_pair_cleanup_plan_apply_preserves_bytes_scope_and_idempotence(self):
        import blogger_release as module
        sources, current = self.live_pair()
        originals = {key: dict(post) for key, (_, post) in current.items()}
        current[('unrelated', 'ko')] = ('LIVE', dict(originals[('ko', 'ko')], id='unrelated', title='unrelated', labels=['ko', 'weekly-ai']))
        plan = module.plan_release(sources, current)
        self.assertEqual(plan, [dict(action='remove_language_switcher', id=lang, key=(lang, lang)) for lang in ('en', 'ko')])
        calls = []
        class Client:
            def update_post(self, post_id, payload):
                calls.append(post_id)
                current[(post_id, post_id)][1].update(payload)
                return current[(post_id, post_id)][1]
        runner = module.ReleaseClient(Client())
        runner.current = lambda: current
        result = runner.apply(sources, plan)
        self.assertEqual(calls, ['ko', 'en'])
        self.assertEqual(len(result['switcher_completed']), 2)
        for key, original in originals.items():
            self.assertEqual(current[key][1], dict(original, content=original['content'].replace('<nav class="oneplate-language-switcher">old</nav>', '')))
        self.assertIn('oneplate-language-switcher', current[('unrelated', 'ko')][1]['content'])
        self.assertEqual(module.plan_release(sources, current), [])

    def test_cleanup_rejects_replaced_post_id_before_any_removal_write(self):
        import blogger_release as module
        sources, current = self.live_pair()
        plan = module.plan_release(sources, current)
        current[('ko', 'ko')][1]['id'] = 'replacement-ko'
        writes = []
        class Client:
            def update_post(self, post_id, payload):
                writes.append(post_id)
                post = next(post for _, post in current.values() if post['id'] == post_id)
                post.update(payload)
                return post
        runner = module.ReleaseClient(Client())
        runner.current = lambda: current
        with self.assertRaises(module.BloggerReleaseError):
            runner.apply(sources, plan)
        self.assertEqual(writes, [])

    def test_later_cleanup_failure_reports_completed_and_attempted_ids(self):
        import blogger_release as module
        sources, current = self.live_pair()
        class Client:
            def update_post(self, post_id, payload):
                if post_id == 'en':
                    raise module.BloggerApiError('later cleanup failed')
                current[(post_id, post_id)][1].update(payload)
                return current[(post_id, post_id)][1]
        runner = module.ReleaseClient(Client())
        runner.current = lambda: current
        with self.assertRaisesRegex(module.BloggerReleaseError, "switcher_completed=.*ko.*failed=.*en"):
            runner.apply(sources, module.plan_release(sources, current))


    def test_mixed_live_draft_pair_plans_existing_live_cleanup(self):
        import blogger_release as module
        sources, current = self.live_pair()
        current[('en', 'en')] = ('DRAFT', dict(current[('en', 'en')][1], content=sources[('en', 'en')]['content']))
        plan = module.plan_release(sources, current)
        self.assertIn(dict(action='remove_language_switcher', id='ko', key=('ko', 'ko')), plan)

    def test_parity_cleanup_plan_contains_exact_content_preserving_series(self):
        import blogger_full_parity_sync as module
        sources, current = self.live_pair()
        for source in sources.values():
            source.update(state='LIVE', path='_posts/test.md')
        plan = module.plan(sources, current)
        for change in plan:
            original = current[tuple(change['key'])][1]['content']
            self.assertEqual(change['content'], original.replace('<nav class="oneplate-language-switcher">old</nav>', ''))
            self.assertTrue(change['remove_language_switcher'])
        for change in plan:
            current[tuple(change['key'])][1]['content'] = change['content']
        self.assertEqual(module.plan(sources, current), [])

    def test_body_update_plan_explicitly_discloses_embedded_cleanup(self):
        import blogger_release as module
        sources, current = self.live_pair()
        current[('ko', 'ko')][1]['content'] = current[('ko', 'ko')][1]['content'].replace('approved ko', 'old ko')
        plan = module.plan_release(sources, current)
        update = next(change for change in plan if change['action'] == 'update_live')
        self.assertTrue(update.get('remove_language_switcher'))
        self.assertNotIn(dict(action='remove_language_switcher', id='ko', key=('ko', 'ko')), plan)

    def test_parity_revert_retains_existing_draft_body_contract(self):
        import blogger_full_parity_sync as module
        sources, current = self.live_pair()
        for source in sources.values():
            source.update(state='DRAFT', path='drafts/test.md')
        for change in module.plan(sources, current):
            self.assertEqual(change['content'], sources[tuple(change['key'])]['content'])

    def test_draft_with_legacy_nav_requires_cleanup_before_promotion(self):
        import blogger_release as module
        sources, current = self.live_pair()
        for key in current:
            current[key] = ('DRAFT', dict(current[key][1], content='<nav class="oneplate-language-switcher">old</nav>' + sources[key]['content']))
        actions = {tuple(change['key']): change['action'] for change in module.plan_release(sources, current) if 'key' in change}
        self.assertEqual(set(actions.values()), {'update_draft_then_publish'})

    def test_mixed_pair_apply_reuses_correct_live_series_and_cleans_legacy_nav(self):
        import blogger_release as module
        sources, current = self.live_pair()
        current[('en', 'en')] = ('DRAFT', dict(current[('en', 'en')][1], content=sources[('en', 'en')]['content']))
        calls = []
        class Client:
            def publish_draft(self, post_id, when):
                current[(post_id, post_id)] = ('LIVE', current[(post_id, post_id)][1])
            def update_post(self, post_id, payload):
                calls.append(post_id)
                current[(post_id, post_id)][1].update(payload)
                return current[(post_id, post_id)][1]
        runner = module.ReleaseClient(Client())
        runner.current = lambda: current
        runner.verify_staged_draft = lambda *_: None
        runner.apply(sources, module.plan_release(sources, current))
        self.assertEqual(module.plan_release(sources, current), [])
        self.assertEqual(calls, ['en', 'ko'])

    def test_old_source_marker_is_removed_without_metadata_change(self):
        from blogger_adapter import build_blogger_payload
        text = ('---\ntitle: Test\nlang: ko\nseries: easy-ai\ntranslation_key: keep\n'
                'permalink: /keep\n---\n'
                '<nav class=oneplate-language-switcher>한국어 · English</nav>\n<p>keep</p>')
        payload = build_blogger_payload(text, public_base_url="https://example.test")
        self.assertNotIn('<nav', payload['content'])
        self.assertEqual(payload['labels'], ['easy-ai', 'ko'])
        self.assertEqual(payload['source_permalink'], '/keep')
        self.assertIn('translation_key: keep', text)

    def test_marker_free_and_similarly_named_content_is_byte_identical(self):
        from blogger_adapter import remove_language_switcher
        content = (' \r\n<p>한국어 · English oneplate-language-switcher</p>'
                   '<nav class="oneplate-language-switcher-extra">menu</nav>'
                   '<!-- <nav class="oneplate-language-switcher">comment</nav> -->'
                   '<script>"<nav class=oneplate-language-switcher>literal</nav>"</script>'
                   '<pre>&lt;nav class=oneplate-language-switcher&gt;</pre>\t\n')
        self.assertEqual(remove_language_switcher(content), content)

    def test_multiple_nested_navs_and_quoted_angle_brackets(self):
        from blogger_adapter import remove_language_switcher
        managed = '<NAV data-x=">" CLASS=oneplate-language-switcher><nav>nested</nav></NAV>'
        self.assertEqual(remove_language_switcher('a' + managed + 'b' + managed + 'c'), 'abc')
        with self.assertRaisesRegex(ValueError, 'Unclosed'):
            remove_language_switcher('<nav class=oneplate-language-switcher><p>uncertain</p>')

    def test_compatibility_entry_removes_only_managed_nav_bytes(self):
        before = '\r\n  <p>한국어 · English: keep prose</p>\n<nav class="other">keep</nav>\t'
        managed = '<nav class="extra oneplate-language-switcher" aria-label="언어 선택"><a>한국어</a> · English</nav>'
        series = '\n<!-- oneplate-series-navigation:start --><section>series</section><!-- oneplate-series-navigation:end -->\n '
        expected = before + series
        actual = inject_language_switcher(before + managed + series, current_lang="ko", ko_url="unused", en_url="unused")
        self.assertEqual(actual.encode(), expected.encode())
        self.assertEqual(inject_language_switcher(actual, current_lang="en", ko_url="unused", en_url="unused"), expected)


if __name__ == '__main__':
    unittest.main()
