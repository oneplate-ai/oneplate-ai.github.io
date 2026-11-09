import unittest
from blogger_draft_guarded_transport import GuardedTransport


class TransportCreateTests(unittest.TestCase):
    def test_only_exact_draft_post_is_forwarded(self):
        class Client:
            blog_id = '42'
            def _request(self, method, path, *, query=None, body=None):
                return {'method': method, 'path': path, 'query': query}
        transport = GuardedTransport(Client())
        self.assertEqual(transport('POST', '/blogs/42/posts', query={'isDraft': 'true'}, body={})['method'], 'POST')
        for path, query in [('/blogs/42/posts/1/publish', {'isDraft': 'true'}), ('/blogs/43/posts', {'isDraft': 'true'}), ('/blogs/42/posts', {})]:
            with self.assertRaises(ValueError):
                transport('POST', path, query=query, body={})


class CliTests(unittest.TestCase):
    def test_create_defaults_to_plan_and_scrubs_errors(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from unittest.mock import patch
        import blogger_draft_cli as cli
        args = ['create', '--blog-id', '42', '--manifest', '/tmp/m', '--manifest-sha256', 'a'*64, '--token-path', '/tmp/t', '--client-secret-path', '/tmp/s']
        with patch.object(cli, 'fresh_access_token', return_value='private-token'), patch.object(cli, 'GuardedTransport'), patch.object(cli, 'BloggerClient'), patch.object(cli.create, 'plan', return_value=[{'action':'create'}]) as plan, patch.object(cli.create, 'run') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
            plan.assert_called_once()
            run.assert_not_called()
        err = io.StringIO()
        with patch.object(cli, 'fresh_access_token', side_effect=RuntimeError('private-token')), redirect_stderr(err):
            self.assertNotEqual(cli.main(args), 0)
        self.assertNotIn('private-token', err.getvalue())

    def test_update_apply_requires_approved_hash(self):
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        import blogger_draft_cli as cli
        args = ['update', '--apply', '--blog-id', '42', '--manifest', '/tmp/m', '--token-path', '/tmp/t', '--client-secret-path', '/tmp/s']
        with patch.object(cli, 'fresh_access_token') as token, redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(args), 0)
            token.assert_not_called()


from test_blogger_draft_create import CreateTests

class CliCreateIntegration(CreateTests):
    def test_cli_plan_and_apply_use_guarded_fake_transport(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        import blogger_draft_cli as cli
        api = self.api
        class Client:
            blog_id = '123'
            def __init__(self, token, blog_id):
                self.blog_id = blog_id
            def _request(self, method, path, *, query=None, body=None):
                return api(method, path, query=query, body=body)
        args = ['create', '--blog-id', '123', '--manifest', str(self.manifest), '--manifest-sha256', self.hash, '--token-path', '/tmp/t', '--client-secret-path', '/tmp/s']
        with patch.object(cli, 'fresh_access_token', return_value='fake'), patch.object(cli, 'BloggerClient', Client), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
            self.assertFalse(any(c[0] == 'POST' for c in api.calls))
            self.assertEqual(cli.main(args + ['--apply']), 0)
        self.assertEqual(sum(c[0] == 'POST' for c in api.calls), 2)


from test_blogger_draft_bound_update import BoundUpdateTests

class CliUpdateIntegration(BoundUpdateTests):
    def test_plan_and_apply_manifest_and_source_binding(self):
        import io, json
        from contextlib import redirect_stdout, redirect_stderr
        from unittest.mock import patch
        import blogger_draft_cli as cli
        common = ['update', '--blog-id', '42', '--token-path', '/tmp/t', '--client-secret-path', '/tmp/s']
        output = io.StringIO()
        with patch.object(cli, 'fresh_access_token', return_value='fake'), patch.object(cli, 'BloggerClient'), patch.object(cli, 'GuardedTransport', return_value=self.api), redirect_stdout(output), redirect_stderr(io.StringIO()):
            manifest_file = self.state / 'manifest.json'
            self.assertEqual(cli.main(common + ['--ko-source', str(self.paths['ko']), '--en-source', str(self.paths['en']), '--image-map', str(self.images), '--ko-id', '1', '--en-id', '2', '--output', str(manifest_file)]), 0)
            self.assertEqual(self.api.puts, [])
            result = json.loads(output.getvalue())
            self.assertNotIn('content', output.getvalue())
            self.assertEqual(manifest_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result['manifest_path'], str(manifest_file))
            original = manifest_file.read_bytes()
            self.assertEqual(cli.main(common + ['--ko-source', str(self.paths['ko']), '--en-source', str(self.paths['en']), '--image-map', str(self.images), '--ko-id', '1', '--en-id', '2', '--output', str(manifest_file)]), 1)
            self.assertEqual(manifest_file.read_bytes(), original)
            self.assertEqual(self.api.puts, [])
            args = common + ['--apply', '--manifest', str(manifest_file), '--manifest-sha256', result['manifest_sha256']]
            self.paths['ko'].write_bytes(b'changed')
            self.assertEqual(cli.main(args), 1)
            self.assertEqual(self.api.puts, [])
            self.paths['ko'].write_bytes(__import__('test_blogger_draft_generic').draft('ko'))
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(self.api.puts, ['1', '2'])
