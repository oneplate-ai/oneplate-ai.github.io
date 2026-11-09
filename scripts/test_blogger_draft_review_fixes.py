import copy
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import blogger_draft_cli as cli
import blogger_draft_update_generic as update
from test_blogger_draft_update_generic import Tests, Fake, bound_plan
from test_blogger_draft_create import CreateTests


class CollisionTests(Tests):
    def test_same_language_titles_are_rejected(self):
        api = Fake()
        from test_blogger_draft_update_generic import inputs
        targets = inputs(api)
        targets['en']['payload']['title'] = targets['ko']['payload']['title']
        with self.assertRaises(update.UpdateError):
            update.plan(api, '42', targets)
        self.assertEqual(api.puts, [])

    def test_collision_before_first_put_blocks_all_writes(self):
        for status in ('LIVE', 'DRAFT', 'SCHEDULED'):
            with self.subTest(status=status):
                api = Fake()
                manifest = bound_plan(api, self.root)
                duplicate = copy.deepcopy(api.posts[1])
                duplicate.update(id='3', status=status)
                api.posts.append(duplicate)
                with patch.object(update, 'GENERIC_STATE_ROOT', self.root / status):
                    with self.assertRaises(update.UpdateError):
                        update.apply(api, manifest, update.manifest_hash(manifest), self.root)
                self.assertEqual(api.puts, [])

    def test_plan_rejects_same_title_in_every_status(self):
        for status in ('LIVE', 'DRAFT', 'SCHEDULED'):
            api = Fake()
            duplicate = copy.deepcopy(api.posts[0])
            duplicate.update(id='3', status=status)
            api.posts.append(duplicate)
            with self.subTest(status=status), self.assertRaises(update.UpdateError):
                bound_plan(api, self.root)
            self.assertEqual(api.puts, [])

    def test_collision_after_first_put_stops_second(self):
        api = Fake()
        manifest = bound_plan(api, self.root)
        def request(method, path, query=None, body=None, headers=None):
            result = api(method, path, query, body, headers)
            if method == 'PUT' and path.endswith('/1'):
                duplicate = copy.deepcopy(api.posts[1])
                duplicate.update(id='3', status='LIVE')
                api.posts.append(duplicate)
            return result
        with self.assertRaises(update.UpdateError):
            update.apply(request, manifest, update.manifest_hash(manifest), self.root)
        self.assertEqual(api.puts, ['1'])


class ManifestCliTests(CreateTests):
    def test_prepare_is_offline_exclusive_and_hashes_actual_bytes(self):
        out = self.root / 'prepared.json'
        args = ['prepare-create', '--blog-id', '123', '--ko-source', str(self.root/'ko'), '--en-source', str(self.root/'en'), '--image-map', str(self.root/'images'), '--output', str(out)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, 'fresh_access_token', side_effect=AssertionError('secret-token')), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(cli.main(args), 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['manifest_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
            manifest = json.loads(out.read_bytes())
            for lang in ('ko', 'en'):
                self.assertEqual(manifest['sources'][lang]['sha256'], hashlib.sha256((self.root/lang).read_bytes()).hexdigest())
            self.assertEqual(manifest['image_map']['sha256'], hashlib.sha256((self.root/'images').read_bytes()).hexdigest())
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cli.create.plan(self.api, '123', out, result['manifest_sha256'])[0]['action'], 'create')
            original = out.read_bytes()
            self.assertEqual(cli.main(args), 1)
            self.assertEqual(out.read_bytes(), original)
        self.assertNotIn('secret-token', stdout.getvalue() + stderr.getvalue())
        self.assertFalse(any(c[0] != 'GET' for c in self.api.calls))

    def test_prepare_rejects_symlink_output(self):
        out = self.root / 'link'
        out.symlink_to(self.manifest)
        original = self.manifest.read_bytes()
        args = ['prepare-create', '--blog-id', '123', '--ko-source', str(self.root/'ko'), '--en-source', str(self.root/'en'), '--image-map', str(self.root/'images'), '--output', str(out)]
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(args), 1)
        self.assertEqual(self.manifest.read_bytes(), original)
