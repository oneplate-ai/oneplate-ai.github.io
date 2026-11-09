import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import blogger_draft_create as create
import blogger_draft_update_generic as update
from test_blogger_draft_create import CreateTests
from test_blogger_draft_update_generic import Fake, inputs, bound_plan


class BoundaryTests(CreateTests):
    def setUp(self):
        super().setUp()
        self.boundary = self.root / 'boundary'
        self.boundary.mkdir()
        for module in (create, update):
            p = patch.object(module, 'GENERIC_STATE_ROOT', self.boundary, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_create_timeout_cannot_replay_with_different_state_dir(self):
        self.api.timeout = True
        with self.assertRaises(create.CreateError):
            create.run(self.api, '123', self.manifest, self.hash, self.root / 'first')
        with self.assertRaises(create.CreateError):
            create.run(self.api, '123', self.manifest, self.hash, self.root / 'second')
        self.assertEqual(sum(c[0] == 'POST' for c in self.api.calls), 1)
        self.assertTrue((self.boundary / 'journals' / ('create-' + self.hash + '.jsonl')).exists())

    def test_update_success_cannot_replay_with_different_state_dir(self):
        api = Fake()
        manifest = bound_plan(api, self.root)
        approved = update.manifest_hash(manifest)
        update.apply(api, manifest, approved, self.root / 'first')
        with self.assertRaises(update.UpdateError):
            update.apply(api, manifest, approved, self.root / 'second')
        self.assertEqual(api.puts, ['1', '2'])
        self.assertTrue((self.boundary / 'journals' / ('update-' + approved + '.jsonl')).exists())

    def test_create_success_blocks_replay_even_against_empty_inventory(self):
        create.run(self.api, '123', self.manifest, self.hash, self.root / 'first')
        fresh = type(self.api)()
        with self.assertRaises(create.CreateError):
            create.run(fresh, '123', self.manifest, self.hash, self.root / 'second')
        self.assertFalse(any(c[0] == 'POST' for c in fresh.calls))

    def test_update_timeout_blocks_replay_against_original_inventory(self):
        api = Fake()
        manifest = bound_plan(api, self.root)
        approved = update.manifest_hash(manifest)
        api.fail_after = True
        with self.assertRaises(update.UpdateError):
            update.apply(api, manifest, approved, self.root / 'first')
        fresh = Fake()
        with self.assertRaises(update.UpdateError):
            update.apply(fresh, manifest, approved, self.root / 'second')
        self.assertEqual(api.puts, ['1'])
        self.assertEqual(fresh.puts, [])

    def test_create_and_update_share_blog_lock(self):
        lock = os.open(self.boundary / '123.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(create.CreateError):
                create.run(self.api, '123', self.manifest, self.hash, self.root / 'one')
            api = Fake()
            manifest = bound_plan(api, self.root)
            manifest['update']['blog_id'] = '123'
            with self.assertRaisesRegex(update.UpdateError, "operation already running"):
                update.apply(api, manifest, update.manifest_hash(manifest), self.root / 'two')
            self.assertEqual(api.puts, [])
            self.assertFalse(any(c[0] == 'POST' for c in self.api.calls))
        finally:
            os.close(lock)
