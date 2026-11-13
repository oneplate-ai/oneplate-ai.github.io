"""Blogger-only contracts, using synthetic source bytes and API boundaries."""
import importlib
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace


class BloggerOnlyTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module('blogger_channel_release')
        except ModuleNotFoundError:
            self.fail('Blogger-only coordinator missing')

    def test_plan_never_constructs_pages_or_materializes_pages_files(self):
        m = self.module()
        unit = SimpleNamespace(series='easy-ai', day='2026-10-15', approval={'publication_unit':'fixture'}, verify_frozen=Mock())
        channel = Mock()
        channel.plan.return_value = {'writes': [], 'inventory': []}
        with patch('dual_channel_release.PagesChannel', side_effect=AssertionError('Pages forbidden')), patch('dual_channel_release.public_files', side_effect=AssertionError('Pages files forbidden')):
            result = m.coordinate(unit, channel)
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(set(result['channels']), {'blogger'})
        channel.plan.assert_called_once_with(unit, {})
        channel.apply.assert_not_called()


    def test_weekly_single_channel_guard_rechecks_bound_inventory_without_fake_parity(self):
        m = self.module()
        import weekly_source_evidence as evidence
        rows = [{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]
        bound = evidence.build_blogger_evidence('2026-10-17', rows)
        unit = SimpleNamespace(series='weekly-ai', day='2026-10-17', approval={'publication_unit':'weekly', 'weekly_evidence':bound}, verify_frozen=Mock())
        channel = Mock()
        channel.daily_inventory.return_value = rows
        channel.plan.return_value = {'writes': [], 'inventory': []}
        with patch('dual_channel_release.check_weekly_evidence', side_effect=AssertionError('no manufactured parity')):
            self.assertEqual(m.coordinate(unit, channel)['status'], 'planned')
        channel.daily_inventory.return_value = [{**rows[0], 'title':'edited'}, rows[1]]
        self.assertEqual(m.coordinate(unit, channel)['status'], 'failed')
        unit.approval['weekly_evidence'] = {k:v for k,v in bound.items() if k not in {'schema_version','source_channel'}}
        self.assertEqual(m.coordinate(unit, channel)['status'], 'failed', 'legacy evidence must not silently become Blogger-only approval')

    def test_producer_cli_is_available_with_fixed_interface(self):
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('blogger_weekly_evidence.py')), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--date', result.stdout)
        self.assertIn('--output', result.stdout)


class ApplySafetyTests(unittest.TestCase):
    def setUp(self):
        from test_dual_channel_release import DualChannelTests
        self.fixture = DualChannelTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.m = importlib.import_module('blogger_channel_release')
        self.unit = self.m.load_unit(self.fixture.root, self.fixture.series, self.fixture.day)
        self.state = self.fixture.root / 'private-state'
        self.posts = {}
        self.calls = []
        self.fail_publish = False

    def request(self, client, method, path, *, query=None, body=None):
        import copy
        self.calls.append((method, path))
        if method == 'GET':
            state = query['status'].upper()
            return {'kind':'blogger#postList', 'etag':'list-etag',
                    'items':copy.deepcopy([p for p in self.posts.values() if p['status'] == state])}
        if path.endswith('/publish'):
            post_id = path.split('/')[-2]
            self.posts[post_id]['status'] = 'LIVE'
            if self.fail_publish:
                raise TimeoutError('synthetic response lost after commit')
            return copy.deepcopy(self.posts[post_id])
        if method == 'PUT':
            post_id = path.split('/')[-1]
            self.posts[post_id].update(copy.deepcopy(body))
            return copy.deepcopy(self.posts[post_id])
        if method == 'POST':
            post_id = str(100 + len(self.posts))
            post = {**copy.deepcopy(body), 'id':post_id, 'kind':'blogger#post',
                    'blog':{'id':'123'}, 'status':'DRAFT', 'etag':'etag-'+post_id,
                    'url':'https://fixture.blogspot.com/'+post_id+'.html',
                    'published':self.unit.approval['scheduled_publish_at']}
            self.posts[post_id] = post
            return copy.deepcopy(post)
        raise AssertionError('unexpected mutation')

    def channel(self):
        m = self.m
        channel = m.BloggerChannel(blog_id='123')
        def factory(unit):
            return m.StrictReleaseClient(m.JournaledBloggerClient('synthetic-token', '123', unit, channel.progress))
        channel.client_factory = factory
        return channel

    def context(self):
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(patch('dual_channel_release.blogger.BloggerClient._request', lambda client, *a, **kw:self.request(client, *a, **kw)))
        stack.enter_context(patch('dual_channel_release.blogger.verify_blogger_public_pair'))
        stack.enter_context(patch('dual_channel_release.draft_create.GENERIC_STATE_ROOT', self.state))
        stack.enter_context(patch('dual_channel_release.PagesChannel', side_effect=AssertionError('no Pages')))
        stack.enter_context(patch('dual_channel_release.public_files', side_effect=AssertionError('no Pages artifacts')))
        return stack

    def test_apply_journals_each_post_and_publish_then_verifies_fresh(self):
        self.assertTrue(hasattr(self.m, 'JournaledBloggerClient'), 'journaled mutation boundary missing')
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'published_verified', result)
        self.assertEqual([m for m, _ in self.calls if m != 'GET'], ['POST'] * 4)
        import json
        journals = list((self.state / 'journals').glob('*.jsonl'))
        self.assertEqual(len(journals), 1)
        events = [json.loads(line) for line in journals[0].read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'mutation_request' for e in events), 4)
        self.assertEqual(sum(e['event'] == 'mutation_response' for e in events), 4)
        self.assertEqual(events[-1]['event'], 'verified')
        with self.context():
            before = len(self.calls)
            retry = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(retry['status'], 'failed')
        self.assertEqual(len(self.calls), before, 'pending journal must block before API reads')

    def test_live_pair_display_only_put_is_journaled_and_unrelated_post_untouched(self):
        import copy
        import json
        for i, source in enumerate(self.unit.sources.values()):
            post_id = str(100 + i)
            self.posts[post_id] = {'kind':'blogger#post', 'id':post_id, 'blog':{'id':'123'}, 'status':'LIVE',
                'title':source['title'], 'content':'<nav class="oneplate-language-switcher">old</nav>'+source['content'],
                'labels':source['labels'], 'url':'https://fixture.blogspot.com/'+post_id+'.html', 'published':source['publish_date']}
        self.posts['999'] = {'kind':'blogger#post','id':'999','blog':{'id':'123'},'status':'DRAFT','title':'Unrelated','content':'private','labels':[]}
        unrelated = copy.deepcopy(self.posts['999'])
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'published_verified', result)
        self.assertEqual([method for method,_ in self.calls if method != 'GET'], ['PUT','PUT'])
        self.assertEqual(self.posts['999'], unrelated)
        events = [json.loads(line) for p in (self.state/'journals').glob('*.jsonl') for line in p.read_text().splitlines()]
        self.assertEqual([e['method'] for e in events if e['event'] == 'mutation_request'], ['PUT','PUT'])
        self.assertEqual([e['method'] for e in events if e['event'] == 'mutation_response'], ['PUT','PUT'])

    def test_exact_target_id_swap_between_replan_and_publish_blocks_send(self):
        with self.context():
            channel = self.channel()
            original = channel.apply
            def swap(unit, files, plan):
                # The executor will inventory/verify the old ID before mutation.
                original_verify = self.m.StrictReleaseClient.verify_staged_draft
                staged_calls = []
                def staged(release, post_id, source):
                    original_verify(release, post_id, source)
                    staged_calls.append(post_id)
                    if len(staged_calls) == 3:
                        post = self.posts.pop(post_id)
                        post['id'] = '999'
                        self.posts['999'] = post
                with patch.object(self.m.StrictReleaseClient, 'verify_staged_draft', staged):
                    return original(unit, files, plan)
            channel.apply = swap
            result = self.m.coordinate(self.unit, channel, apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any(path.endswith('/publish') for method,path in self.calls if method != 'GET'), result)

    def test_future_apply_has_no_api_or_local_state_writes(self):
        self.unit.approval['scheduled_publish_at'] = '2099-10-15T19:00:00+09:00'
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())

    def test_raw_title_collision_without_labels_is_not_ignored(self):
        source = next(iter(self.unit.sources.values()))
        self.posts['999'] = {'kind':'blogger#post', 'id':'999', 'blog':{'id':'123'}, 'status':'SCHEDULED', 'title':source['title'], 'content':'private unrelated', 'labels':[]}
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any(method != 'GET' for method,_ in self.calls))

    def test_weekly_live_evidence_drift_after_replan_blocks_mutation(self):
        # Exercise the per-request guard without fabricating a second channel.
        import weekly_source_evidence as evidence
        rows = [{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]
        self.unit.series, self.unit.day = 'weekly-ai', '2026-10-17'
        self.unit.approval['weekly_evidence'] = evidence.build_blogger_evidence(self.unit.day, rows)
        inventories = [rows, rows, [{**rows[0], 'title':'drift'}, rows[1]]]
        with self.context(), patch.object(self.unit, 'verify_frozen'), patch.object(self.m.BloggerChannel, 'daily_inventory', side_effect=inventories):
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(any(method != 'GET' for method,_ in self.calls))

    def test_state_symlink_cannot_create_journals_outside_fixed_root(self):
        elsewhere = self.fixture.root / 'elsewhere'
        elsewhere.mkdir()
        self.state.symlink_to(elsewhere, target_is_directory=True)
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_incomplete_or_repeated_inventory_blocks_before_writes(self):
        for response in [{}, {'kind':'blogger#postList','etag':'etag','items':[{}]},
                         {'kind':'blogger#postList','etag':'etag','items':[], 'nextPageToken':'same'}]:
            with self.subTest(response=response), self.context(), patch('dual_channel_release.blogger.BloggerClient._request', return_value=response):
                result = self.m.coordinate(self.unit, self.channel())
            self.assertEqual(result['status'], 'failed')

    def test_edited_draft_or_live_body_is_preserved(self):
        for state in ['DRAFT', 'LIVE']:
            self.calls.clear()
            self.posts.clear()
            source = next(iter(self.unit.sources.values()))
            self.posts['999'] = {'kind':'blogger#post', 'id':'999', 'blog':{'id':'123'}, 'status':state, 'title':source['title'], 'content':'independent edit', 'labels':source['labels']}
            with self.subTest(state=state), self.context():
                result = self.m.coordinate(self.unit, self.channel(), apply=True)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(self.posts['999']['content'], 'independent edit')
            self.assertFalse(any(method != 'GET' for method,_ in self.calls))

    def test_plan_drift_and_public_readback_failure_are_not_success(self):
        with self.context():
            channel = self.channel()
            original_plan = channel.plan
            calls = []
            def drifting(unit, files):
                import copy
                plan = copy.deepcopy(original_plan(unit, files))
                calls.append(plan)
                if len(calls) == 2:
                    plan['writes'].reverse()
                return plan
            channel.plan = drifting
            result = self.m.coordinate(self.unit, channel, apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any(method != 'GET' for method,_ in self.calls))
        with self.context(), patch('dual_channel_release.blogger.verify_blogger_public_pair', side_effect=RuntimeError('anonymous URL missing')):
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['channels']['blogger']['stage'], 'verify')
        self.assertEqual(len(result['channels']['blogger']['completed']['published']), 2)

    def test_frozen_artifact_drift_during_verification_never_marks_journal_verified(self):
        import json
        def changed(*args):
            self.fixture.image_map.write_bytes(b'{ }')
        with self.context(), patch('dual_channel_release.blogger.verify_blogger_public_pair', side_effect=changed):
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        events = [json.loads(line) for p in (self.state/'journals').glob('*.jsonl') for line in p.read_text().splitlines()]
        self.assertNotIn('verified', [e['event'] for e in events])

    def test_guard_cannot_publish_a_live_inventory_id_without_planned_promotion(self):
        import copy
        source = next(iter(self.unit.sources.values()))
        self.posts['100'] = {'kind':'blogger#post','id':'100','blog':{'id':'123'},'status':'LIVE',
                             'title':source['title'],'labels':source['labels'],'content':source['content']}
        with self.context():
            client = self.channel().client_factory(self.unit).client
            client.authorize({'inventory':[{'id':'100','key':source['key'],'sha256':self.m.base.fingerprint(self.posts['100'])}], 'writes':[]})
            client.recorder = Mock()
            with self.assertRaises(self.m.ReleaseError):
                client._request('POST', '/blogs/123/posts/100/publish', query={'publishDate':self.unit.approval['scheduled_publish_at']})
        self.assertFalse(any(method != 'GET' for method,_ in self.calls))

    def test_source_changed_after_first_create_prevents_later_mutations(self):
        original = self.request
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1] == 'POST':
                draft = next(p for p in self.unit.frozen if p.parent.name == 'drafts')
                draft.write_bytes(draft.read_bytes() + b'changed')
            return result
        self.request = changed
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(sum(method != 'GET' for method,_ in self.calls), 1)

    def test_timeout_retains_partial_ids_and_blocks_automatic_replay(self):
        self.assertTrue(hasattr(self.m, 'JournaledBloggerClient'), 'journaled mutation boundary missing')
        self.fail_publish = True
        with self.context():
            result = self.m.coordinate(self.unit, self.channel(), apply=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('do not retry', result['channels']['blogger']['error'])
        completed = result['channels']['blogger']['completed']
        self.assertEqual(len(completed['mutations']), 2, result)
        self.assertEqual(completed['uncertain']['id'], '100')
        self.assertEqual([p['status'] for p in self.posts.values()].count('LIVE'), 1)
        self.assertEqual(len([p for m,p in self.calls if p.endswith('/publish')]), 1)


if __name__ == '__main__':
    unittest.main()
