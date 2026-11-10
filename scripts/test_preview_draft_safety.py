import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import blogger_release as blogger
import test_dual_channel_release as dual_tests


class DraftProtectionTests(unittest.TestCase):
    def setUp(self):
        self.source = {'key': ('제목', 'ko'), 'title': '제목', 'language': 'ko',
                       'labels': ['today-ai-bite-ko'], 'content': '<p>approved</p>',
                       'publish_date': '2000-01-01T00:00:00Z'}

    def test_different_draft_body_blocks_instead_of_update(self):
        post = {'id': '1', 'title': '제목', 'labels': ['today-ai-bite-ko'], 'content': '<p>edited</p>'}
        with self.assertRaisesRegex(blogger.BloggerReleaseError, 'DRAFT'):
            blogger.plan_release({self.source['key']: self.source}, {self.source['key']: ('DRAFT', post)})

    def test_single_channel_cli_apply_is_blocked_before_any_external_access(self):
        from unittest.mock import patch
        with patch.object(blogger, 'fresh_access_token') as token, patch.object(blogger, 'validate_approval') as approval:
            rc = blogger.main(['--approval', '/nonexistent', '--image-urls', '/nonexistent', '--apply'])
        self.assertEqual(rc, 1)
        token.assert_not_called()
        approval.assert_not_called()

    def test_direct_stale_update_action_never_writes(self):
        client = Mock()
        runner = blogger.ReleaseClient(client)
        with self.assertRaises(blogger.BloggerReleaseError):
            runner.apply({self.source['key']: self.source}, [{'action': 'update_draft_then_publish', 'id': '1', 'key': self.source['key']}])
        client.update_draft.assert_not_called()
        client.publish_draft.assert_not_called()


class BindingProtectionTests(unittest.TestCase):
    def test_binding_accepts_exact_ids_and_rejects_pending_journal(self):
        m = dual_tests.DualChannelTests(methodName='runTest').module()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'bindings').mkdir()
            (state / 'journals').mkdir()
            (state / 'bindings' / '2026-11-11-today-ai-bite.json').write_text(json.dumps({
                'version': 1, 'blog_id': '123', 'day': '2026-11-11', 'series': 'today-ai-bite',
                'ids': {'ko': '1', 'en': '2'}, 'manifest_sha256': 'a' * 64}))
            unit = Mock(day='2026-11-11', series='today-ai-bite', sources={
                ('ko', 'ko'): {'language': 'ko'}, ('en', 'en'): {'language': 'en'}})
            current = {('ko', 'ko'): ('DRAFT', {'id': '1'}), ('en', 'en'): ('DRAFT', {'id': '2'})}
            m.verify_preview_binding(unit, '123', current, state)
            (state / 'journals' / 'pending.jsonl').write_text(json.dumps({'blog_id': '123'}) + '\n')
            with self.assertRaises(blogger.DraftConflictError):
                m.verify_preview_binding(unit, '123', current, state)

    def test_renamed_or_unlabelled_preview_draft_blocks_release(self):
        m = dual_tests.DualChannelTests(methodName='runTest').module()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'bindings').mkdir()
            (state / 'bindings' / '2026-11-11-today-ai-bite.json').write_text(json.dumps({
                'version': 1, 'blog_id': '123', 'day': '2026-11-11', 'series': 'today-ai-bite',
                'ids': {'ko': '1', 'en': '2'}, 'manifest_sha256': 'a' * 64}))
            unit = Mock(day='2026-11-11', series='today-ai-bite', sources={
                ('ko', 'ko'): {'language': 'ko'}, ('en', 'en'): {'language': 'en'}})
            with self.assertRaises(blogger.DraftConflictError):
                m.verify_preview_binding(unit, '123', {('en', 'en'): ('DRAFT', {'id': '2'})}, state)


class DualChannelDraftProtection(unittest.TestCase):
    def setUp(self):
        self.helper = dual_tests.DualChannelTests(methodName='runTest')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_each_language_series_label_conflict_stops_pages_using_real_planner(self):
        m = self.helper.module()
        unit = self.helper.unit()
        for lang in ('ko', 'en'):
            with self.subTest(lang=lang):
                pages, blog = Mock(), Mock()
                pages.plan.return_value = {'writes': ['article']}
                source = next(s for s in unit.sources.values() if s['language'] == lang)
                post = {'id': '12', 'title': source['title'], 'labels': ['weekly-ai-' + lang], 'content': source['content']}
                blog.plan.side_effect = lambda *_: blogger.plan_release(unit.sources, {source['key']: ('DRAFT', post)})
                result = m.coordinate(unit, {'pages': pages, 'blogger': blog}, apply=True)
                self.assertNotEqual(result['status'], 'published_verified')
                pages.apply.assert_not_called()
                blog.apply.assert_not_called()

    def test_blogger_plan_drift_before_pages_blocks_both_writes(self):
        m = self.helper.module()
        pages, blog = Mock(), Mock()
        pages.plan.return_value = {'writes': ['article']}
        blog.plan.side_effect = [{'writes': ['old']}, {'writes': ['new']}]
        result = m.coordinate(self.helper.unit(), {'pages': pages, 'blogger': blog}, apply=True)
        self.assertNotEqual(result['status'], 'published_verified')
        pages.apply.assert_not_called()
        blog.apply.assert_not_called()

    def test_blogger_unclassified_planning_error_blocks_both_channels(self):
        m = self.helper.module()
        pages, blog = Mock(), Mock()
        pages.plan.return_value = {'writes': ['article']}
        blog.plan.side_effect = blogger.BloggerReleaseError('duplicate or series mismatch')
        result = m.coordinate(self.helper.unit(), {'pages': pages, 'blogger': blog}, apply=True)
        self.assertNotEqual(result['status'], 'published_verified')
        pages.apply.assert_not_called()
        blog.apply.assert_not_called()

    def test_blogger_draft_conflict_prevents_pages_write(self):
        m = self.helper.module()
        pages, blog = Mock(), Mock()
        pages.plan.return_value = {'writes': ['article']}
        blog.plan.side_effect = blogger.DraftConflictError('DRAFT mismatch')
        result = m.coordinate(self.helper.unit(), {'pages': pages, 'blogger': blog}, apply=True)
        self.assertNotEqual(result['status'], 'published_verified')
        pages.apply.assert_not_called()
        blog.apply.assert_not_called()

    def test_draft_change_after_initial_plan_stops_pages_before_write(self):
        m = self.helper.module()
        pages, blog = Mock(), Mock()
        pages.plan.return_value = {'writes': ['article']}
        blog.plan.side_effect = [{'writes': []}, blogger.DraftConflictError('DRAFT changed')]
        result = m.coordinate(self.helper.unit(), {'pages': pages, 'blogger': blog}, apply=True)
        self.assertNotEqual(result['status'], 'published_verified')
        pages.apply.assert_not_called()
        blog.apply.assert_not_called()
