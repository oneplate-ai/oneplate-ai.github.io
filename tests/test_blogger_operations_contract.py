"""Blogger 전용 운영 경로와 Pages 자동배포 분리를 검증한다."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BloggerOperationsContractTests(unittest.TestCase):
    def test_production_workflow_cannot_deploy_pages(self):
        text = (ROOT / '.github/workflows/pages-deploy.yml').read_text()
        self.assertNotIn('actions/deploy-pages', text)
        self.assertNotIn('actions/upload-pages-artifact', text)
        self.assertNotRegex(text, r'(?m)^\s+(?:push|pull_request):')
        self.assertNotRegex(text, r'(?m)^\s+pages:\s*write')

    def test_legacy_static_validation_is_manual_only(self):
        for name in ['jekyll-build.yml', 'validate-site.yml']:
            with self.subTest(workflow=name):
                text = (ROOT / '.github/workflows' / name).read_text()
                self.assertNotRegex(text, r'(?m)^\s+(?:push|pull_request):')
                self.assertIn('workflow_dispatch:', text)

    def test_blogger_ci_runs_runtime_and_operating_contract(self):
        path = ROOT / '.github/workflows/blogger-operations.yml'
        self.assertTrue(path.is_file(), 'Blogger 운영 CI가 필요합니다.')
        text = path.read_text()
        self.assertIn('pull_request:', text)
        self.assertIn('push:', text)
        self.assertIn('fetch-depth: 2', text)
        self.assertIn("unittest discover -s scripts -p 'test_*.py'", text)
        self.assertIn("unittest discover -s tests -p 'test_*.py'", text)
        self.assertNotIn('jekyll', text.lower())
        self.assertNotIn('actions/deploy-pages', text)

    def test_existing_four_cron_jobs_use_only_blogger(self):
        path = ROOT / 'docs/BLOGGER_ONLY_CRON_PROPOSAL.json'
        self.assertTrue(path.is_file(), 'Blogger 예약 전환 제안이 필요합니다.')
        data = json.loads(path.read_text())
        expected = {'237dd1616856': '0 12 * * 1,3,5', '573788297ddb': '0 19 * * 1,3,5',
                    'ac4e7ab54ea3': '30 19 * * 5', '2b2c1a1e93a9': '0 19 * * 6'}
        self.assertEqual({j['id']: j['schedule'] for j in data['jobs']}, expected)
        for job in data['jobs']:
            with self.subTest(job=job['id']):
                self.assertIn('Blogger', job['prompt'])
                self.assertIn('BLOGGER_ONLY_PUBLICATION.md', job['prompt'])
                self.assertNotIn('dual_channel_release.py', job['prompt'])
                self.assertNotIn('weekly_source_evidence.py', job['prompt'])
                self.assertNotRegex(job['prompt'], r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]+')
                if job['role'] == 'publish':
                    self.assertIn('blogger_channel_release.py', job['prompt'])
                if job['series'] == 'weekly-ai' and job['role'] == 'preview':
                    self.assertIn('blogger_weekly_evidence.py', job['prompt'])
                self.assertEqual(job['workdir'], '/root/oneplate-ai.github.io')
                self.assertTrue(job['attach_to_session'])
                self.assertNotIn('deliver', job)
                self.assertNotIn('origin', job)
                self.assertNotIn('model', job)


    def test_success_journal_reconciliation_runbook_is_required(self):
        path = ROOT / 'docs' / 'BLOGGER_PUBLISH_JOURNAL_RUNBOOK.md'
        self.assertTrue(path.is_file(), 'publish journal reconciliation procedure missing')
        text = path.read_text()
        for term in ('flock', 'approval_sha256', 'mutation_response', 'verified',
                     'LIVE', 'os.link', 'fsync', 'os.unlink', 'reconcile-create'):
            self.assertIn(term, text)
        proposal = json.loads((ROOT / 'docs' / 'BLOGGER_ONLY_CRON_PROPOSAL.json').read_text())
        for job in proposal['jobs']:
            if job['role'] == 'publish':
                self.assertIn('BLOGGER_PUBLISH_JOURNAL_RUNBOOK.md', job['prompt'])

if __name__ == '__main__':
    unittest.main()
