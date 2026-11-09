import copy
import hashlib
import importlib
import tempfile
import unittest
from pathlib import Path

from test_blogger_draft_generic import draft
from test_blogger_draft_update_generic import Fake
from blogger_draft_generic import prepare_pair


def digest(b):
    return hashlib.sha256(b).hexdigest()


class BoundUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = {lang: root / lang for lang in ('ko', 'en')}
        for lang, path in self.paths.items(): path.write_bytes(draft(lang))
        self.images = root / 'images.json'
        self.images.write_bytes(b'{}')
        self.state = root / 'state'
        self.state.mkdir()
        import blogger_draft_update_generic as updater
        from unittest.mock import patch
        if hasattr(updater, 'GENERIC_STATE_ROOT'):
            p = patch.object(updater, 'GENERIC_STATE_ROOT', root / 'isolated-state')
            p.start()
            self.addCleanup(p.stop)
        self.api = Fake()
        pair = prepare_pair({k: p.read_bytes() for k, p in self.paths.items()}, {k: digest(p.read_bytes()) for k, p in self.paths.items()}, b'{}', digest(b'{}'))
        for lang, post in zip(('ko','en'), self.api.posts):
            post.update(title=pair[lang]['title'], labels=copy.deepcopy(pair[lang]['labels']))
        self.ids = {'ko':'1','en':'2'}

    def module(self):
        try: return importlib.import_module('blogger_draft_bound_update')
        except ModuleNotFoundError: self.fail('bound update adapter missing')

    def test_prepared_payload_written_to_exact_pair(self):
        m = self.module()
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        self.assertEqual(self.api.puts, [])
        m.apply(self.api, plan, m.manifest_hash(plan), self.paths, self.images, self.state)
        self.assertEqual(self.api.puts, ['1', '2'])
        self.assertIn('Hello', self.api.posts[0]['content'])

    def test_wrong_identity_is_rejected(self):
        m = self.module()
        for field, value in [('title','wrong'), ('labels',['weekly-ai-en'])]:
            with self.subTest(field=field):
                original = copy.deepcopy(self.api.posts[0][field])
                self.api.posts[0][field] = value
                with self.assertRaises(Exception): m.plan(self.api, '42', self.ids, self.paths, self.images)
                self.api.posts[0][field] = original
        self.assertEqual(self.api.puts, [])

    def test_source_drift_before_each_write_blocks_remaining_writes(self):
        m = self.module()
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        def request(method, path, query=None, body=None, headers=None):
            result = self.api(method, path, query, body, headers)
            if method == 'PUT': self.paths['en'].write_bytes(draft('en') + b'changed')
            return result
        with self.assertRaises(Exception): m.apply(request, plan, m.manifest_hash(plan), self.paths, self.images, self.state)
        self.assertEqual(self.api.puts, ['1'])

    def test_payload_or_map_tamper_even_with_reapproved_manifest_rejected(self):
        m = self.module()
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        plan['update']['targets']['ko']['payload']['content'] = 'forged'
        with self.assertRaises(Exception): m.apply(self.api, plan, m.manifest_hash(plan), self.paths, self.images, self.state)
        self.assertEqual(self.api.puts, [])
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        self.images.write_bytes(b'{ }')
        with self.assertRaises(Exception): m.apply(self.api, plan, m.manifest_hash(plan), self.paths, self.images, self.state)
        self.assertEqual(self.api.puts, [])

    def test_direct_apply_rejects_reapproved_identity_change(self):
        import blogger_draft_update_generic as updater
        m = self.module()
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        self.api.posts[0]['title'] = 'other title'
        plan['update']['targets']['ko']['metadata_sha256'] = updater.metadata_hash(self.api.posts[0])
        with self.assertRaises(updater.UpdateError):
            updater.apply(self.api, plan, updater.manifest_hash(plan), self.state)
        self.assertEqual(self.api.puts, [])

    def test_direct_apply_rechecks_image_map_before_second_write(self):
        import blogger_draft_update_generic as updater
        m = self.module()
        plan = m.plan(self.api, '42', self.ids, self.paths, self.images)
        def changing(method, path, query=None, body=None, headers=None):
            result = self.api(method, path, query, body, headers)
            if method == 'PUT': self.images.write_bytes(b'{ }')
            return result
        with self.assertRaises(updater.UpdateError):
            updater.apply(changing, plan, updater.manifest_hash(plan), self.state)
        self.assertEqual(self.api.puts, ['1'])
