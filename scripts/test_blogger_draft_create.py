import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from blogger_draft_generic import prepare_pair
from blogger_draft_plan import digest
from test_blogger_draft_generic import draft, sha
import blogger_draft_create as create


class Fake:
    def __init__(self):
        self.posts = []
        self.calls = []
        self.timeout = False

    def __call__(self, method, path, *, query=None, body=None):
        self.calls.append((method, path, query))
        if method == 'GET':
            return {'kind': 'blogger#postList', 'etag': 'e', 'items': [p.copy() for p in self.posts if p['status'].lower() == query['status']]}
        post = dict(body, kind='blogger#post', blog={'id': '123'}, id=str(len(self.posts)+10), status='DRAFT')
        self.posts.append(post)
        if self.timeout:
            raise TimeoutError()
        return {'id': post['id']}


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.object(create, "GENERIC_STATE_ROOT", self.root / "boundary")
        patcher.start()
        self.addCleanup(patcher.stop)
        sources = {lang: draft(lang) for lang in ('ko', 'en')}
        images = b'{}'
        payloads = prepare_pair(sources, {k: sha(v) for k, v in sources.items()}, images, sha(images))
        for name, data in dict(sources, images=images).items():
            (self.root / name).write_bytes(data)
        manifest = {'blog_id': '123', 'sources': {k: {'path': str(self.root / k), 'sha256': sha(v)} for k, v in sources.items()}, 'image_map': {'path': str(self.root / 'images'), 'sha256': sha(images)}, 'payload_sha256': {k: digest(v) for k, v in payloads.items()}}
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps(manifest))
        self.hash = sha(self.manifest.read_bytes())
        self.api = Fake()

    def test_timeout_blocks_retry_and_records_request(self):
        self.api.timeout = True
        with self.assertRaises(create.CreateError):
            create.run(self.api, '123', self.manifest, self.hash, self.root / 'state')
        with self.assertRaises(create.CreateError):
            create.run(self.api, '123', self.manifest, self.hash, self.root / 'state')
        self.assertEqual(sum(c[0] == 'POST' for c in self.api.calls), 1)
        events = [json.loads(x)['event'] for x in (self.root / 'boundary' / 'journals' / ('create-' + self.hash + '.jsonl')).read_text().splitlines()]
        self.assertEqual(events, ['intent', 'request'])

    def test_existing_titles_any_status_block(self):
        for status in ('LIVE', 'DRAFT', 'SCHEDULED'):
            with self.subTest(status=status):
                self.api.posts = [dict(kind='blogger#post', blog={'id': '123'}, id='55', status=status, title='Title ko', content='x', labels=[])]
                with self.assertRaises(ValueError):
                    create.plan(self.api, '123', self.manifest, self.hash)
        self.assertFalse(any(c[0] == 'POST' for c in self.api.calls))

    def test_changed_source_and_wrong_blog_fail_without_posts(self):
        (self.root / 'ko').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            create.plan(self.api, '123', self.manifest, self.hash)
        with self.assertRaises(ValueError):
            create.plan(self.api, '456', self.manifest, self.hash)
        self.assertFalse(self.api.calls)

    def test_previously_created_draft_change_stops_second_completion(self):
        api = self.api
        original = api.__call__
        class Mutating:
            def __call__(self, method, path, *, query=None, body=None):
                result = original(method, path, query=query, body=body)
                if method == 'POST' and len(api.posts) == 2:
                    api.posts[0]['content'] = 'changed'
                return result
        with self.assertRaises(create.CreateError):
            create.run(Mutating(), '123', self.manifest, self.hash, self.root / 'state')

    def test_plan_no_write_then_create_pair_and_readback(self):
        plan = create.plan(self.api, '123', self.manifest, self.hash)
        self.assertEqual([p['action'] for p in plan], ['create', 'create'])
        self.assertFalse(any(c[0] == 'POST' for c in self.api.calls))
        result = create.run(self.api, '123', self.manifest, self.hash, self.root / 'state')
        self.assertEqual(result, {'ko': '10', 'en': '11'})
        self.assertEqual([c[2] for c in self.api.calls if c[0] == 'POST'], [{'isDraft': 'true'}]*2)
        events = [json.loads(x)['event'] for x in (self.root / 'boundary' / 'journals' / ('create-' + self.hash + '.jsonl')).read_text().splitlines()]
        self.assertEqual(events, ['intent', 'request', 'response', 'request', 'response', 'complete'])
