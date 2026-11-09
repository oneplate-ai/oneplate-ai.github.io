import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import blogger_draft_update_generic as mod
import blogger_draft_bound_update as bound
from blogger_draft_generic import prepare_pair
from test_blogger_draft_generic import draft
import hashlib

class Fake:
    def __init__(self):
        self.posts = [dict(kind='blogger#post', id=str(i), blog={'id':'42'}, status='DRAFT', etag='"v1"', title=lang, content='old', labels=[lang], url='url'+lang, published='2026-01-01T00:00:00Z') for i, lang in [(1,'ko'),(2,'en')]]
        raw = {lang: draft(lang) for lang in ('ko', 'en')}
        pair = prepare_pair(raw, {k: hashlib.sha256(v).hexdigest() for k,v in raw.items()}, b'{}', hashlib.sha256(b'{}').hexdigest())
        for lang, post in zip(('ko','en'), self.posts):
            post.update(title=pair[lang]['title'], labels=pair[lang]['labels'])
        self.puts = []
        self.fail_after = False
    def __call__(self, method, path, query=None, body=None, headers=None):
        if method == 'GET':
            return {'kind':'blogger#postList','etag':'x','items':copy.deepcopy([p for p in self.posts if p['status'].lower()==query['status']])}
        assert query == {'isDraft':'true'}
        post = next(p for p in self.posts if p['id']==path.rsplit('/',1)[1])
        if headers != {'If-Match': post['etag']}: raise RuntimeError('412')
        self.puts.append(post['id'])
        post.update(copy.deepcopy(body))
        if self.fail_after: raise TimeoutError()
        return copy.deepcopy(post)

def inputs(api):
    raw = {lang: draft(lang) for lang in ('ko', 'en')}
    pair = prepare_pair(raw, {k: hashlib.sha256(v).hexdigest() for k,v in raw.items()}, b'{}', hashlib.sha256(b'{}').hexdigest())
    return {lang: {'id': post['id'], 'body_sha256': mod.body_hash(post), 'metadata_sha256': mod.metadata_hash(post), 'payload': pair[lang]} for lang, post in zip(('ko', 'en'), api.posts)}

def bound_plan(api, root):
    paths = {lang: root / ('source-' + lang) for lang in ('ko', 'en')}
    for lang, path in paths.items(): path.write_bytes(draft(lang))
    image = root / 'image-map.json'
    image.write_bytes(b'{}')
    return bound.plan(api, '42', {'ko':'1', 'en':'2'}, paths, image)

class Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(mod, "GENERIC_STATE_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
    def test_invalid_published_on_either_target_blocks_all_writes(self):
        for value in (None, '', 'invalid', '2026-02-30T00:00:00Z'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                api = Fake(); api.posts[1]['published'] = value
                root = Path(d); manifest = bound_plan(api, root)
                with patch.object(mod, 'GENERIC_STATE_ROOT', root / 'state'):
                    with self.assertRaises(mod.UpdateError):
                        mod.apply(api, manifest, mod.manifest_hash(manifest), root)
                self.assertEqual(api.puts, [])

    def test_put_preserves_backed_up_published_on_resetting_server(self):
        api = Fake(); manifest = bound_plan(api, self.root); sent = []
        original_dates = [p['published'] for p in api.posts]
        def resetting_server(method, path, query=None, body=None, headers=None):
            if method == 'PUT':
                sent.append(copy.deepcopy(body))
                post = next(p for p in api.posts if p['id'] == path.rsplit('/', 1)[1])
                if 'published' not in body:
                    post['published'] = '2026-11-09T00:00:00Z'
            return api(method, path, query, body, headers)
        mod.apply(resetting_server, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual([body['published'] for body in sent], original_dates)
        self.assertEqual([p['published'] for p in api.posts], original_dates)

    def test_server_changes_published_stops_before_second_write(self):
        api = Fake(); manifest = bound_plan(api, self.root)
        def drift(method, path, query=None, body=None, headers=None):
            result = api(method, path, query, body, headers)
            if method == 'PUT':
                api.posts[0]['published'] = '2026-11-09T00:00:00Z'
                result['published'] = api.posts[0]['published']
            return result
        with self.assertRaises(mod.UpdateError):
            mod.apply(drift, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(api.puts, ['1'])
        journal = (self.root / 'journals' / ('update-' + mod.manifest_hash(manifest) + '.jsonl')).read_text()
        self.assertIn('failure-readback', journal)

    def test_readback_published_drift_stops_before_second_write(self):
        api = Fake(); manifest = bound_plan(api, self.root)
        def drift(method, path, query=None, body=None, headers=None):
            result = api(method, path, query, body, headers)
            if method == 'PUT': api.posts[0]['published'] = '2026-11-09T00:00:00Z'
            return result
        with self.assertRaises(mod.UpdateError):
            mod.apply(drift, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(api.puts, ['1'])

    def test_direct_unbound_entry_rejects_without_writes(self):
        api = Fake()
        manifest = mod.plan(api, '42', inputs(api))
        with self.assertRaises(mod.UpdateError):
            mod.apply(api, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(api.puts, [])
    def test_plan_then_apply_and_journal_blocks_replay(self):
        api=Fake()
        with tempfile.TemporaryDirectory() as d:
            manifest=bound_plan(api,self.root)
            self.assertEqual(api.puts,[])
            mod.apply(api, manifest, mod.manifest_hash(manifest), Path(d))
            self.assertEqual(api.puts,['1','2'])
            with self.assertRaises(mod.UpdateError): mod.apply(api,manifest,mod.manifest_hash(manifest),Path(d))
            self.assertEqual(api.puts,['1','2'])
    def test_drift_prevents_any_write(self):
        api=Fake(); manifest=bound_plan(api,self.root); api.posts[0]['content']='user edit'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mod.UpdateError): mod.apply(api,manifest,mod.manifest_hash(manifest),Path(d))
        self.assertEqual(api.puts,[])
    def test_ambiguous_failure_is_not_retried(self):
        api=Fake(); manifest=bound_plan(api,self.root); api.fail_after=True
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mod.UpdateError): mod.apply(api,manifest,mod.manifest_hash(manifest),Path(d))
            with self.assertRaises(mod.UpdateError): mod.apply(api,manifest,mod.manifest_hash(manifest),Path(d))
            self.assertEqual(api.puts,['1'])
            self.assertIn('failure-readback', (self.root/'journals'/('update-'+mod.manifest_hash(manifest)+'.jsonl')).read_text())
    def test_second_target_edit_after_first_write_is_preserved(self):
        api=Fake(); manifest=bound_plan(api,self.root)
        def changing(method, path, query=None, body=None, headers=None):
            result = api(method, path, query, body, headers)
            if method == 'PUT' and path.endswith('/1'):
                api.posts[1]['content']='human edit'
            return result
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mod.UpdateError): mod.apply(changing,manifest,mod.manifest_hash(manifest),Path(d))
        self.assertEqual(api.puts,['1'])
        self.assertEqual(api.posts[1]['content'],'human edit')
    def test_manifest_tamper_and_scheduled_rejected(self):
        api=Fake(); manifest=bound_plan(api,self.root); approved=mod.manifest_hash(manifest)
        manifest['update']['targets']['en']['payload']['content']='tampered'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mod.UpdateError): mod.apply(api,manifest,approved,Path(d))
        self.assertEqual(api.puts,[])
        spec=inputs(api); api.posts[0]['status']='SCHEDULED'
        with self.assertRaises(mod.UpdateError): mod.plan(api,'42',spec)
    def test_live_and_metadata_drift_block(self):
        api=Fake(); spec=inputs(api); api.posts[1]['status']='LIVE'
        with self.assertRaises(mod.UpdateError): mod.plan(api,'42',spec)
        api.posts[1]['status']='DRAFT'; api.posts[1]['url']='changed'
        with self.assertRaises(mod.UpdateError): mod.plan(api,'42',spec)

    def test_unresolved_other_manifest_blocks_blog_and_saves_backup(self):
        api = Fake(); first = bound_plan(api, self.root); api.fail_after = True
        with self.assertRaises(mod.UpdateError): mod.apply(api, first, mod.manifest_hash(first), self.root)
        journal = (self.root / 'journals' / ('update-' + mod.manifest_hash(first) + '.jsonl')).read_text()
        self.assertIn('"content": "old"', journal)
        api.fail_after = False
        second = bound_plan(api, self.root)
        with self.assertRaises(mod.UpdateError): mod.apply(api, second, mod.manifest_hash(second), self.root)
        self.assertEqual(api.puts, ['1'])

    def test_exception_traceback_does_not_expose_secret(self):
        import traceback
        api = Fake(); manifest = bound_plan(api, self.root)
        reached = []
        def fail(method, path, query=None, body=None, headers=None):
            if method == 'PUT':
                reached.append(True)
                raise RuntimeError('VERY_PRIVATE_SECRET')
            return api(method, path, query, body, headers)
        try: mod.apply(fail, manifest, mod.manifest_hash(manifest), self.root)
        except mod.UpdateError as exc: self.assertNotIn('VERY_PRIVATE_SECRET', ''.join(traceback.format_exception(exc)))
        else: self.fail('write should fail')
        self.assertEqual(reached, [True])

    def test_concurrent_edit_at_put_is_rejected_by_if_match(self):
        api = Fake()
        for p in api.posts: p['etag'] = '"v1"'
        manifest = bound_plan(api, self.root)
        seen = []
        def conditional(method, path, query=None, body=None, headers=None):
            if method == 'PUT':
                post = next(p for p in api.posts if p['id'] == path.rsplit('/', 1)[1])
                post.update(content='human edit', etag='"v2"')
                seen.append(headers)
                if not headers or headers.get('If-Match') != post['etag']:
                    raise RuntimeError('412 Precondition Failed')
            return api(method, path, query, body)
        with self.assertRaises(mod.UpdateError):
            mod.apply(conditional, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(seen, [{'If-Match': '"v1"'}])
        self.assertEqual(api.posts[0]['content'], 'human edit')
        self.assertEqual(api.puts, [])

    def test_missing_etag_blocks_write(self):
        api = Fake(); manifest = bound_plan(api, self.root)
        del api.posts[0]['etag']
        with self.assertRaises(mod.UpdateError): mod.apply(api, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(api.puts, [])

    def test_guarded_transport_sends_if_match_without_modifying_publisher(self):
        from blogger_draft_guarded_transport import GuardedTransport
        import io, json
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        class Client:
            blog_id = '42'
            access_token = 'fake'
            def _request(self, method, path, *, query=None, body=None): return {'read': True}
            def opener(self, request, timeout):
                self.request = request
                return Response(json.dumps({'ok': True}).encode())
        client = Client(); transport = GuardedTransport(client)
        self.assertEqual(transport('GET', '/blogs/42/posts', query={'status':'draft'}), {'read': True})
        self.assertEqual(transport('PUT', '/blogs/42/posts/1', query={'isDraft':'true'}, body={'content':'x'}, headers={'If-Match':'"v1"'}), {'ok':True})
        self.assertEqual(client.request.get_header('If-match'), '"v1"')
        with self.assertRaises(ValueError): transport('PUT', '/blogs/42/posts/1', query={'isDraft':'true'}, body={}, headers={})

    def test_each_write_uses_its_latest_inventory_etag(self):
        api = Fake(); manifest = bound_plan(api, self.root); seen = []
        def request(method, path, query=None, body=None, headers=None):
            if method == 'PUT': seen.append(headers['If-Match'])
            result = api(method, path, query, body, headers)
            if method == 'PUT' and path.endswith('/1'): api.posts[1]['etag'] = '"v2"'
            return result
        mod.apply(request, manifest, mod.manifest_hash(manifest), self.root)
        self.assertEqual(seen, ['"v1"', '"v2"'])

    def test_guarded_transport_412_is_not_retried(self):
        from blogger_draft_guarded_transport import GuardedTransport
        from urllib.error import HTTPError
        class Client:
            blog_id = '42'; access_token = 'fake'; calls = 0
            def opener(self, request, timeout):
                self.calls += 1
                raise HTTPError(request.full_url, 412, 'Precondition Failed', {}, None)
        client = Client()
        with self.assertRaises(RuntimeError):
            GuardedTransport(client)('PUT', '/blogs/42/posts/1', query={'isDraft':'true'}, body={}, headers={'If-Match':'"v1"'})
        self.assertEqual(client.calls, 1)
