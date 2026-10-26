#!/usr/bin/env python3
"""Local fixtures; external command/API boundaries are never executed."""
import copy
import hashlib
import importlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class DualChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'drafts').mkdir()
        self.directory = self.root / '.publishing-approvals'
        self.directory.mkdir()
        self.day = '2026-10-15'
        self.series = 'easy-ai'
        self.stem = self.day + '-easy-ai-07'
        self.record = {
            'schema_version': 2, 'series': self.series,
            'publication_unit': 'easy-ai-2026-10-15-bilingual',
            'approved_at': '2026-10-14T10:00:00+09:00',
            'scheduled_publish_at': '2026-10-15T19:00:00+09:00',
            'approval_text_literal': '한영 원문 그대로 승인',
            'blogger_image_map_sha256': digest(b'{}'), 'drafts': [],
        }
        for lang, title in [('ko', '승인 제목'), ('en', 'Approved title')]:
            name = self.stem + ('-en' if lang == 'en' else '') + '.md'
            permalink = ('/en' if lang == 'en' else '') + '/posts/approved.html'
            text = (f'---\ntitle: "{title}"\nseries: easy-ai\nlang: {lang}\n'
                    f'date: {self.day}\ntranslation_key: easy-ai-07\npermalink: {permalink}\n'
                    'published: false\ndraft: true\nnoindex: true\n---\n'
                    '<p>Exact prose. This draft is quoted here.</p>\n'
                    '<p class="post-verification">This draft was checked.</p>\n')
            path = self.root / 'drafts' / name
            path.write_text(text)
            self.record['drafts'].append(dict(lang=lang, title=title, date=self.day,
                translation_key='easy-ai-07', permalink=permalink,
                exact_draft_path='drafts/' + name, sha256=digest(path.read_bytes())))
        self.approval = self.directory / (self.stem + '.approval.json')
        self.save()
        self.image_map = self.directory / (self.stem + '.blogger-image-map.json')
        self.image_map.write_bytes(b'{}')

    def save(self, path=None, record=None):
        (path or self.approval).write_text(json.dumps(record or self.record, ensure_ascii=False))

    def module(self):
        try:
            return importlib.import_module('dual_channel_release')
        except ModuleNotFoundError:
            self.fail('dual_channel_release implementation missing')

    def unit(self):
        return self.module().load_unit(self.root, self.series, self.day)

    def test_resolve_exact_episode_approval_and_freeze_pair_map(self):
        m = self.module()
        unit = self.unit()
        self.assertEqual(unit.approval_path, self.approval)
        self.assertEqual(unit.image_map_path, self.image_map)
        self.assertEqual(len(unit.sources), 2)
        unit.verify_frozen()
        self.image_map.write_bytes(b'{ }')
        with self.assertRaisesRegex(m.ReleaseError, 'changed'):
            unit.verify_frozen()

    def test_public_copy_changes_only_state_and_exact_verification_class(self):
        import blogger_release
        text = (self.root / self.record['drafts'][0]['exact_draft_path']).read_text()
        text += ('<aside class="verification-note">이 초안은 확인했습니다.</aside>\n'
                 '<p class="not-post-verification">This draft is prose.</p>\n')
        actual = blogger_release.public_copy_from_draft(text)
        expected = text.replace('published: false', 'published: true').replace('draft: true\n', '').replace('noindex: true\n', '')
        expected = expected.replace('This draft was checked.', 'This post was checked.').replace('이 초안은 확인했습니다.', '이번 게시글은 확인했습니다.')
        self.assertEqual(actual, expected)

    def test_pages_plan_is_exact_pair_noop_or_conflict(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'public_files'), 'public file planner missing')
        unit = self.unit()
        files = m.public_files(unit)
        self.assertEqual(set(files), {'_posts/' + Path(d['exact_draft_path']).name for d in self.record['drafts']})
        self.assertEqual(m.plan_pages(files, {}), sorted(files))
        self.assertEqual(m.plan_pages(files, files), [])
        live = dict(files)
        live[next(iter(files))] = b'different LIVE body'
        with self.assertRaisesRegex(m.ReleaseError, 'conflict'):
            m.plan_pages(files, live)

    def add_image(self, name='approved.svg'):
        raw_map = json.dumps({name: 'https://blogger.googleusercontent.com/img/a.svg'}).encode()
        self.image_map.write_bytes(raw_map)
        self.record['blogger_image_map_sha256'] = digest(raw_map)
        for item in self.record['drafts']:
            path = self.root / item['exact_draft_path']
            path.write_text(path.read_text() + '<img src="{{ \'/assets/images/' + name + '\' | relative_url }}">\n')
            item['sha256'] = digest(path.read_bytes())
        self.save()

    def test_approved_image_missing_fails_then_exact_asset_is_frozen(self):
        m = self.module()
        self.add_image()
        unit = self.unit()
        with self.assertRaisesRegex(m.ReleaseError, 'missing.*asset'):
            m.public_files(unit)
        folder = self.root / 'drafts/images'
        folder.mkdir()
        asset = folder / 'approved.svg'
        asset.write_bytes(b'<svg>approved</svg>')
        files = m.public_files(unit)
        self.assertEqual(files['assets/images/approved.svg'], asset.read_bytes())
        asset.write_bytes(b'<svg>changed</svg>')
        with self.assertRaisesRegex(m.ReleaseError, 'changed'):
            unit.verify_frozen()

    def test_coordinator_retains_success_on_partial_failure_and_reinventories_retry(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'coordinate'), 'coordinator missing')
        unit = self.unit()
        pages, blog = Mock(), Mock()
        pages.plan.return_value = {'writes': ['approved pair']}
        blog.plan.return_value = {'writes': ['approved Blogger keys']}
        pages.apply.return_value = {'commit': 'abc'}
        blog.apply.side_effect = RuntimeError('publish failed, id=42')
        result = m.coordinate(unit, {'pages': pages, 'blogger': blog}, apply=True)
        self.assertEqual(result['status'], 'partial_failure')
        self.assertEqual(result['channels']['pages']['status'], 'published_verified')
        self.assertEqual(result['channels']['pages']['completed'], {'commit': 'abc'})
        self.assertEqual(result['channels']['blogger']['stage'], 'apply')
        first_calls = pages.plan.call_count
        pages.plan.return_value = {'writes': []}
        blog.apply.side_effect = None
        blog.apply.return_value = {'published': ['42', '43']}
        retry = m.coordinate(unit, {'pages': pages, 'blogger': blog}, apply=True)
        self.assertEqual(retry['status'], 'published_verified')
        self.assertGreater(pages.plan.call_count, first_calls)

    def test_schedule_guard_precedes_all_channel_calls(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'coordinate'), 'coordinator missing')
        self.record['scheduled_publish_at'] = '2099-10-15T19:00:00+09:00'
        self.save()
        unit = self.unit()
        channel = Mock()
        with self.assertRaisesRegex(Exception, '미래|future'):
            m.coordinate(unit, {'pages': channel}, apply=True)
        self.assertEqual(channel.mock_calls, [])
        channel.plan.return_value = {'writes': ['pair']}
        self.assertEqual(m.coordinate(unit, {'pages': channel})['status'], 'planned')
        channel.apply.assert_not_called()

    def test_plan_drift_blocks_channel_before_any_mutation(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'coordinate'), 'coordinator missing')
        channel = Mock()
        channel.plan.side_effect = [{'writes': ['a']}, {'writes': ['b']}]
        result = m.coordinate(self.unit(), {'pages': channel}, apply=True)
        self.assertEqual(result['channels']['pages']['stage'], 'replan')
        self.assertIn('drift', result['channels']['pages']['error'])
        channel.apply.assert_not_called()

    def test_pages_executor_clean_isolation_explicit_stage_normal_push(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'PagesChannel'), 'production Pages executor missing')
        unit = self.unit()
        files = m.public_files(unit)
        calls = []
        head = 'a' * 40
        commit = 'b' * 40
        def run(args, cwd=None):
            calls.append((args, cwd))
            if args[:2] == ['git', 'clone']:
                Path(args[-1]).mkdir()
                return b''
            if args[:2] == ['git', 'rev-parse']:
                return ((commit if args[-1] == 'HEAD' else head) + '\n').encode()
            if args[:3] == ['git', 'status', '--porcelain=v1']:
                return b''
            if args[:3] == ['git', 'diff', '--name-only']:
                return '\0'.join(sorted(files)).encode() + b'\0'
            if args[:2] == ['git', 'ls-files']:
                return b''
            return b''
        channel = m.PagesChannel(work_root=self.root / 'scratch', run=run)
        channel.progress = {}
        completed = channel.apply(unit, files, {'head': head, 'writes': sorted(files)})
        self.assertEqual(completed['commit'], commit)
        self.assertTrue(any(a == ['git', 'push', 'origin', 'HEAD:main'] for a, _ in calls))
        staged = next(a for a, _ in calls if a[:2] == ['git', 'add'])
        self.assertEqual(staged, ['git', 'add', '--'] + sorted(files))
        self.assertFalse(any('--force' in a or '-f' in a for a, _ in calls))
        self.assertFalse((self.root / '_posts').exists())
        validators = [' '.join(a) for a, _ in calls if 'docker' in a]
        self.assertTrue(any('jekyll build' in s for s in validators))
        self.assertTrue(any('validate_bilingual_publication.rb' in s for s in validators))
        self.assertTrue(any('validate_editorial_rules.rb --strict' in s for s in validators))
        count = len(calls)
        channel.apply(unit, files, {'head': head, 'writes': []})
        self.assertEqual(len(calls), count, 'identical live pair must not repush')

    def test_remote_inventory_is_pinned_to_main_sha_and_rejects_truncation(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'RemoteTree'), 'remote inventory missing')
        head = 'a' * 40
        blob = 'b' * 40
        responses = [json.dumps({'object': {'sha': head}}), json.dumps({'tree': [
            {'path': '_posts/exact.md', 'mode': '100644', 'type': 'blob', 'sha': blob}], 'truncated': False}),
            json.dumps({'encoding': 'base64', 'content': 'ZXhhY3Q='})]
        run = Mock(side_effect=[x.encode() for x in responses])
        tree = m.RemoteTree('oneplate-ai/oneplate-ai.github.io', run)
        self.assertEqual(tree.read(['_posts/exact.md', '_posts/missing.md']), {'_posts/exact.md': b'exact'})
        self.assertEqual(tree.head, head)
        self.assertIn(head, run.call_args_list[1].args[0][-1])
        run = Mock(side_effect=[responses[0].encode(), b'{"tree": [], "truncated": true}'])
        with self.assertRaisesRegex(m.ReleaseError, 'truncated'):
            m.RemoteTree('oneplate-ai/oneplate-ai.github.io', run)

    def web_fixture(self, unit):
        base = 'https://oneplate-ai.github.io'
        urls = {d['lang']: base + d['permalink'] for d in unit.approval['drafts']}
        resources = {}
        for item in unit.approval['drafts']:
            prefix = '/en' if item['lang'] == 'en' else ''
            html = (f'<title>{item["title"]}</title><link rel="canonical" href="{urls[item["lang"]]}">'
                    f'<link rel="alternate" hreflang="ko-KR" href="{urls["ko"]}">'
                    f'<link rel="alternate" hreflang="en" href="{urls["en"]}">'
                    '<link rel="stylesheet" href="/assets/site.css">')
            resources[urls[item['lang']]] = (200, {}, html.encode())
            resources[base + prefix + '/'] = (200, {}, f'<a href="{item["permalink"]}">{item["title"]}</a>'.encode())
            resources[base + prefix + '/series/easy-ai/'] = (200, {}, item['title'].encode())
        resources[base + '/sitemap.xml'] = (200, {}, ('<urlset>' + ''.join('<url><loc>' + u + '</loc></url>' for u in urls.values()) + '</urlset>').encode())
        resources[base + '/assets/site.css'] = (200, {'content-type': 'text/css; charset=utf-8'}, b'body {}')
        return resources

    def test_pages_public_verifier_requires_canonical_hreflang_sitemap_css_and_no_robots(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'verify_pages_web'), 'complete Pages verifier missing')
        unit = self.unit()
        resources = self.web_fixture(unit)
        fetch = lambda url: resources[url]
        m.verify_pages_web(unit, 'https://oneplate-ai.github.io', fetch=fetch)
        post = 'https://oneplate-ai.github.io' + self.record['drafts'][0]['permalink']
        for marker, replacement in [(b'rel="canonical"', b'rel="unrelated"'),
                                    (b'hreflang="en"', b'hreflang="fr"')]:
            original = resources[post]
            resources[post] = (200, {}, original[2].replace(marker, replacement))
            with self.assertRaises(m.ReleaseError):
                m.verify_pages_web(unit, 'https://oneplate-ai.github.io', fetch=fetch)
            resources[post] = original
        resources[post] = (200, {}, resources[post][2] + b'<meta name="robots" content="noindex">')
        with self.assertRaisesRegex(m.ReleaseError, 'robots|noindex'):
            m.verify_pages_web(unit, 'https://oneplate-ai.github.io', fetch=fetch)

    def test_pages_wait_is_commit_specific_and_bounded(self):
        m = self.module()
        self.assertTrue(hasattr(m.PagesChannel, 'wait_deploy'), 'bounded deployment waiter missing')
        run = Mock(return_value=b'[{"status":"completed", "conclusion":"success"}]')
        channel = m.PagesChannel(run=run)
        channel.wait_deploy('a' * 40, timeout=0)
        self.assertIn('a' * 40, run.call_args.args[0])
        channel.run = Mock(return_value=b'[]')
        with self.assertRaisesRegex(m.ReleaseError, 'timeout'):
            channel.wait_deploy('a' * 40, timeout=0)

    def test_blogger_adapter_scopes_inventory_reuses_release_and_independently_verifies(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'BloggerChannel'), 'production Blogger adapter missing')
        unit = self.unit()
        release = Mock()
        release.current.return_value = {}
        verified = Mock()
        verified.verify.return_value = {}
        factory = Mock(side_effect=[release, verified])
        channel = m.BloggerChannel(client_factory=factory)
        files = m.public_files(unit)
        plan = channel.plan(unit, files)
        release.current.assert_called_once_with(set(unit.sources))
        self.assertEqual(len([c for c in plan['writes'] if c['action'] == 'create_draft_then_publish']), 2)
        release.apply.return_value = {'published': [{'id': '1'}, {'id': '2'}]}
        self.assertEqual(channel.apply(unit, files, plan), release.apply.return_value)
        with patch.object(m.blogger, 'verify_blogger_public_pair') as urls, patch.object(m.blogger, 'plan_release', return_value=[]):
            channel.verify(unit, files, {})
            verified.verify.assert_called_once_with(unit.sources)
            urls.assert_called_once_with(unit.sources, {})
        self.assertEqual(factory.call_count, 2)

    def test_blogger_adapter_blocks_live_body_rewrite_and_binds_ids(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'BloggerChannel'), 'production Blogger adapter missing')
        unit = self.unit()
        source = next(iter(unit.sources.values()))
        release = Mock()
        release.current.return_value = {source['key']: ('LIVE', {
            'id': 'live-77', 'title': source['title'], 'labels': source['labels'],
            'url': 'https://example.test/post', 'content': 'different live prose'})}
        channel = m.BloggerChannel(client_factory=lambda unit: release)
        with self.assertRaisesRegex(m.ReleaseError, 'LIVE.*conflict'):
            channel.plan(unit, m.public_files(unit))

    def test_blogger_adapter_preserves_live_posts_with_approved_presentation_variant(self):
        m = self.module()
        unit = self.unit()
        current = {}
        for key, source in unit.sources.items():
            current[key] = ('LIVE', {
                'id': 'live-' + key[1],
                'title': source['title'],
                'labels': source['labels'],
                'url': 'https://example.test/' + key[1],
                'content': source['unformatted_content'],
            })
        release = Mock()
        release.current.return_value = current
        channel = m.BloggerChannel(client_factory=lambda _unit: release)

        plan = channel.plan(unit, m.public_files(unit))

        self.assertEqual(plan['writes'], [])

    def test_weekly_actual_sets_and_bound_approval_inventory_must_all_match(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'check_weekly_evidence'), 'weekly parity gate missing')
        unit = self.unit()
        unit.series = 'weekly-ai'
        unit.day = '2026-10-17'
        posts = [dict(date='2026-10-12', lang=lang, title=title) for lang, title in [('ko', '일간'), ('en', 'Daily')]]
        self.assertEqual(m.weekly_window(unit.day), ('2026-10-10', '2026-10-16'))
        with self.assertRaisesRegex(m.ReleaseError, 'weekly_evidence_mismatch'):
            m.check_weekly_evidence(unit, posts, [])
        with self.assertRaisesRegex(m.ReleaseError, 'weekly_evidence_unbound'):
            m.check_weekly_evidence(unit, posts, posts)
        unit.approval['weekly_evidence'] = dict(start='2026-10-10', end='2026-10-16', posts=posts)
        evidence = m.check_weekly_evidence(unit, posts, list(reversed(posts)))
        self.assertEqual(len(evidence), 2)
        stale = copy.deepcopy(posts)
        stale[0]['title'] = 'stale approved inventory'
        unit.approval['weekly_evidence']['posts'] = stale
        with self.assertRaisesRegex(m.ReleaseError, 'weekly_evidence_mismatch'):
            m.check_weekly_evidence(unit, posts, posts)

    def test_weekly_coordinator_blocks_both_channels_before_writes(self):
        m = self.module()
        unit = self.unit()
        unit.series = 'weekly-ai'
        unit.day = '2026-10-17'
        pages, blog = Mock(), Mock()
        pages.daily_inventory.return_value = [dict(date='2026-10-12', lang='ko', title='일간')]
        blog.daily_inventory.return_value = []
        # Preserve fixture's frozen paths; weekly identity is the input under test.
        with patch.object(unit, 'verify_frozen'), patch.object(m, 'public_files', return_value={}):
            with self.assertRaisesRegex(m.ReleaseError, 'weekly_evidence_mismatch'):
                m.coordinate(unit, {'pages': pages, 'blogger': blog}, apply=True)
        pages.apply.assert_not_called()
        blog.apply.assert_not_called()

    def test_guarded_blogger_checks_artifacts_before_each_exact_mutation(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'GuardedBloggerClient'), 'per-mutation artifact guard missing')
        unit = self.unit()
        progress = {}
        client = m.GuardedBloggerClient('not-a-real-token', '123', unit, progress)
        source = next(iter(unit.sources.values()))
        client.authorize({'inventory': [], 'writes': [{'action': 'create_draft_then_publish', 'key': source['key']}]})
        payload = {k: source[k] for k in ('title', 'labels', 'content')}
        with patch.object(m.blogger.BloggerClient, '_request', return_value={'id': 'new-1'}) as api:
            client._request('POST', '/blogs/123/posts', query={'isDraft': 'true'}, body=payload)
            self.assertEqual(progress['mutations'][0]['id'], 'new-1')
            with self.assertRaisesRegex(m.ReleaseError, 'scope'):
                client._request('PUT', '/blogs/123/posts/unapproved', body=payload)
            path = self.root / self.record['drafts'][0]['exact_draft_path']
            path.write_text(path.read_text() + '<p>changed</p>')
            with self.assertRaisesRegex(m.ReleaseError, 'changed'):
                client._request('POST', '/blogs/123/posts/new-1/publish')
            self.assertEqual(api.call_count, 1)

    def test_failure_report_retains_commit_and_exact_executor_stage(self):
        m = self.module()
        channel = Mock()
        channel.plan.return_value = {'writes': ['pair']}
        channel.progress = {'commit': 'retained-commit', 'stage': 'push', 'worktree': '/scratch/evidence'}
        channel.apply.side_effect = RuntimeError('push rejected')
        result = m.coordinate(self.unit(), {'pages': channel}, apply=True)
        failure = result['channels']['pages']
        self.assertEqual(failure.get('completed', {}).get('commit'), 'retained-commit')
        self.assertEqual(failure.get('executor_stage'), 'push')

    def test_weekly_collectors_use_remote_public_sources_and_blogger_live_only(self):
        m = self.module()
        self.assertTrue(hasattr(m.PagesChannel, 'daily_inventory'), 'Pages weekly collector missing')
        self.assertTrue(hasattr(m.BloggerChannel, 'daily_inventory'), 'Blogger weekly collector missing')
        raw = b'---\nseries: today-ai-bite\nlang: en\ndate: 2026-10-12\ntitle: Daily\npermalink: /en/posts/daily.html\n---\nbody'
        tree = Mock()
        tree.entries = {'_posts/2026-10-12-today-ai-bite-en.md': {}, 'drafts/private.md': {}}
        tree.read.return_value = {'_posts/2026-10-12-today-ai-bite-en.md': raw}
        with patch.object(m, 'RemoteTree', return_value=tree), patch.object(m, 'fetch_url', return_value=(200, {}, b'<title>Daily</title>')):
            pages = m.PagesChannel().daily_inventory(self.unit(), '2026-10-10', '2026-10-16')
        tree.read.assert_called_once_with(['_posts/2026-10-12-today-ai-bite-en.md'])
        release = Mock()
        release.list_posts.return_value = [dict(id='1', title='Daily', labels=['today-ai-bite-en'], published='2026-10-12T10:00:00Z'),
                                          dict(id='2', title='Weekly', labels=['weekly-ai-en'], published='2026-10-12T10:00:00Z')]
        blog = m.BloggerChannel(client_factory=lambda unit: release).daily_inventory(self.unit(), '2026-10-10', '2026-10-16')
        self.assertEqual(pages, blog)
        release.list_posts.assert_called_once_with('LIVE')

    def test_cli_defaults_to_read_only_plan_without_creating_work_root(self):
        import contextlib
        import io
        m = self.module()
        self.assertTrue(hasattr(m, 'main'), 'CLI missing')
        channel = Mock()
        channel.plan.return_value = {'writes': ['exact pair']}
        scratch = self.root / 'must-not-exist'
        with patch.object(m, 'PagesChannel', return_value=channel), patch.object(m, 'BloggerChannel', return_value=channel), contextlib.redirect_stdout(io.StringIO()) as output:
            status = m.main(['--root', str(self.root), '--series', self.series, '--date', self.day,
                             '--image-urls', str(self.image_map), '--work-root', str(scratch)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['mode'], 'plan')
        channel.apply.assert_not_called()
        self.assertFalse(scratch.exists())

    def test_default_map_prefers_canonical_series_name_for_episode_approval(self):
        m = self.module()
        canonical_map = self.directory / (self.day + '-easy-ai.blogger-image-map.json')
        self.image_map.rename(canonical_map)
        unit = m.load_unit(self.root, self.series, self.day)
        self.assertEqual(unit.image_map_path, canonical_map)

    def test_conflicting_weekly_bound_evidence_is_not_ignored_in_candidate_resolution(self):
        m = self.module()
        other = copy.deepcopy(self.record)
        other['weekly_evidence'] = {'start': '2026-10-10', 'end': '2026-10-16', 'posts': []}
        self.save(self.directory / (self.stem + '.blogger-release.json'), other)
        with self.assertRaisesRegex(m.ReleaseError, 'conflicting approvals'):
            self.unit()

    def test_draft_symlink_outside_editorial_root_is_rejected(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as outside:
            old = self.root / 'drafts'
            for path in old.iterdir():
                path.rename(Path(outside) / path.name)
            old.rmdir()
            old.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(m.ReleaseError, 'escapes'):
                self.unit()

    def test_missing_approval_wrong_series_date_path_hash_and_map_fail_closed(self):
        m = self.module()
        from publication_gate import GateError
        original = copy.deepcopy(self.record)
        for kind in ('series', 'date', 'path', 'hash', 'map'):
            with self.subTest(kind=kind):
                self.record = copy.deepcopy(original)
                if kind == 'series':
                    self.record['series'] = 'weekly-ai'
                elif kind == 'date':
                    self.record['drafts'][0]['date'] = '2026-10-14'
                elif kind == 'path':
                    self.record['drafts'][0]['exact_draft_path'] = '../secret.md'
                elif kind == 'hash':
                    self.record['drafts'][0]['sha256'] = '0' * 64
                else:
                    self.record['blogger_image_map_sha256'] = '0' * 64
                self.save()
                with self.assertRaises((GateError, m.ReleaseError, m.blogger.BloggerReleaseError)):
                    self.unit()
        self.approval.unlink()
        with self.assertRaisesRegex(m.ReleaseError, 'missing approval'):
            self.unit()

    def test_discovery_validates_all_candidates_and_explicit_series_date_equally(self):
        m = self.module()
        alias = self.directory / (self.stem + '.blogger-release.json')
        alias.write_bytes(self.approval.read_bytes())
        self.assertEqual(self.unit().approval_path, self.approval)
        alternate = copy.deepcopy(self.record)
        alternate['scheduled_publish_at'] = '2026-10-15T20:00:00+09:00'
        self.save(alias, alternate)
        with self.assertRaisesRegex(m.ReleaseError, 'conflicting approvals'):
            m.load_unit(self.root, self.series, self.day, self.approval, self.image_map)
        alias.unlink()
        with self.assertRaises(Exception):
            m.load_unit(self.root, 'weekly-ai', self.day, self.approval, self.image_map)
        with self.assertRaises(Exception):
            m.load_unit(self.root, self.series, '2026-10-14', self.approval, self.image_map)

    def test_home_series_link_is_valid_for_older_idempotent_pair(self):
        m = self.module()
        unit = self.unit()
        resources = self.web_fixture(unit)
        for prefix in ('', '/en'):
            resources['https://oneplate-ai.github.io' + prefix + '/'] = (200, {},
                f'<a href="{prefix}/series/easy-ai/">Series</a>'.encode())
        m.verify_pages_web(unit, 'https://oneplate-ai.github.io', fetch=lambda url: resources[url])

    def test_unknown_map_keys_block_even_when_pair_has_no_images(self):
        m = self.module()
        raw = b'{"unrelated.svg":"https://blogger.googleusercontent.com/img/x.svg"}'
        self.image_map.write_bytes(raw)
        self.record['blogger_image_map_sha256'] = digest(raw)
        self.save()
        with self.assertRaisesRegex(m.ReleaseError, 'unknown'):
            m.public_files(self.unit())

    def test_guard_refuses_an_unplanned_draft_creation(self):
        m = self.module()
        unit = self.unit()
        client = m.GuardedBloggerClient('not-a-real-token', '123', unit, {})
        client.authorize({'inventory': [], 'writes': []})
        source = next(iter(unit.sources.values()))
        with patch.object(m.blogger.BloggerClient, '_request', return_value={'id': 'unexpected'}) as api:
            with self.assertRaisesRegex(m.ReleaseError, 'unplanned'):
                client._request('POST', '/blogs/123/posts', query={'isDraft': 'true'},
                                body={k: source[k] for k in ('title', 'labels', 'content')})
            api.assert_not_called()

    def test_pages_apply_rechecks_materialized_bytes_after_build_before_staging(self):
        m = self.module()
        unit = self.unit()
        files = m.public_files(unit)
        calls = []
        head = 'a' * 40
        def run(args, cwd=None):
            calls.append(args)
            if args[:2] == ['git', 'clone']:
                Path(args[-1]).mkdir()
            if args[:2] == ['git', 'rev-parse']:
                return head.encode()
            if args[0] == 'docker':
                (cwd / next(iter(files))).write_bytes(b'changed by build')
            if args[:3] == ['git', 'diff', '--name-only']:
                return '\0'.join(files).encode() + b'\0'
            return b''
        channel = m.PagesChannel(work_root=self.root / 'scratch', run=run)
        with self.assertRaisesRegex(m.ReleaseError, 'materialized'):
            channel.apply(unit, files, {'head': head, 'writes': sorted(files)})
        self.assertFalse(any(a[:2] == ['git', 'add'] for a in calls))

    def test_bodyless_blogger_create_is_rejected_before_api(self):
        m = self.module()
        unit = self.unit()
        client = m.GuardedBloggerClient('not-a-real-token', '123', unit, {})
        client.authorize({'inventory': [], 'writes': []})
        with patch.object(m.blogger.BloggerClient, '_request', return_value={'id': 'unexpected'}) as api:
            with self.assertRaisesRegex(m.ReleaseError, 'payload'):
                client._request('POST', '/blogs/123/posts', query={'isDraft': 'true'})
            api.assert_not_called()

    def test_missing_channel_cannot_report_dual_publication_verified(self):
        m = self.module()
        channel = Mock()
        channel.plan.return_value = {'writes': []}
        channel.apply.return_value = {'commit': 'already-live'}
        result = m.coordinate(self.unit(), {'pages': channel}, apply=True)
        self.assertNotEqual(result['status'], 'published_verified')

    def test_bound_weekly_inventory_cannot_hide_out_of_window_claims(self):
        m = self.module()
        unit = self.unit()
        unit.day = '2026-10-17'
        posts = [dict(date='2026-10-12', lang=lang, title=title) for lang, title in [('ko', '일간'), ('en', 'Daily')]]
        extra = dict(date='2026-10-01', lang='ko', title='Unrelated old daily')
        unit.approval['weekly_evidence'] = dict(start='2026-10-10', end='2026-10-16', posts=posts + [extra])
        with self.assertRaisesRegex(m.ReleaseError, 'weekly_evidence_mismatch'):
            m.check_weekly_evidence(unit, posts, posts)


if __name__ == '__main__':
    unittest.main()
