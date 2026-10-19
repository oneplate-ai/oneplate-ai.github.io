import importlib
import unittest

class WeeklyEvidenceTests(unittest.TestCase):
    def test_builds_exact_paired_week_inventory_and_rejects_channel_drift(self):
        try:
            m = importlib.import_module('weekly_source_evidence')
        except ModuleNotFoundError:
            self.fail('weekly_source_evidence implementation missing')
        posts = [{'date':'2026-10-12','lang':'ko','title':'2026년 10월 12일'}, {'date':'2026-10-12','lang':'en','title':'October 12, 2026'}]
        evidence = m.build_evidence('2026-10-17', posts, list(reversed(posts)))
        self.assertEqual(evidence, {'start':'2026-10-10','end':'2026-10-16','posts':sorted(posts,key=lambda x:(x['date'],x['lang'],x['title']))})
        with self.assertRaises(ValueError):
            m.build_evidence('2026-10-17', posts, posts[:1])
        with self.assertRaises(ValueError):
            m.validate_evidence('2026-10-17', {**evidence,'posts':posts[:1]})

    def test_approval_creator_binds_weekly_evidence_and_rejects_missing_binding(self):
        import json
        import subprocess
        import sys
        from pathlib import Path
        from test_dual_channel_release import DualChannelTests
        fixture = DualChannelTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for d in fixture.record['drafts']:
            path = fixture.root / d['exact_draft_path']
            text = path.read_text().replace('series: easy-ai', 'series: weekly-ai').replace('date: 2026-10-15', 'date: 2026-10-17').replace('translation_key: easy-ai-07', 'translation_key: weekly-ai-2026-10-17')
            path.write_text(text)
        evidence = {'start':'2026-10-10','end':'2026-10-16','posts':[{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]}
        evidence['posts'].sort(key=lambda item:(item['date'],item['lang'],item['title']))
        epath = fixture.root / 'evidence.json'
        epath.write_text(json.dumps(evidence))
        output = fixture.directory / 'weekly.approval.json'
        cmd = [sys.executable, str(Path(__file__).with_name('create_approval_record.py')), '--root',str(fixture.root),'--ko',str(fixture.root/fixture.record['drafts'][0]['exact_draft_path']),'--en',str(fixture.root/fixture.record['drafts'][1]['exact_draft_path']),'--approval-text','approved','--scheduled-publish-at','2026-10-17T19:00:00+09:00','--approved-at','2026-10-16T20:00:00+09:00','--output',str(output),'--blogger-image-map',str(fixture.image_map)]
        unbound = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(unbound.returncode, 0, 'new weekly approvals must bind reviewed evidence')
        self.assertFalse(output.exists(), 'unbound record must not be written')
        bound = subprocess.run(cmd + ['--weekly-evidence', str(epath)], capture_output=True, text=True)
        self.assertEqual(bound.returncode, 0, bound.stderr)
        self.assertEqual(json.loads(output.read_text())['weekly_evidence'], evidence)

    def test_export_cli_reads_both_and_never_overwrites_reviewed_evidence(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, Mock
        import weekly_source_evidence as m
        rows = [{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]
        pages, blog = Mock(), Mock()
        pages.daily_inventory.return_value = rows
        blog.daily_inventory.return_value = rows
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'PagesChannel', return_value=pages), patch.object(m, 'BloggerChannel', return_value=blog):
            output = Path(tmp)/'evidence.json'
            self.assertEqual(m.main(['--date','2026-10-17','--output',str(output)]),0)
            before = output.read_bytes()
            self.assertEqual(len(json.loads(before)['posts']),2)
            self.assertEqual(m.main(['--date','2026-10-17','--output',str(output)]),1)
            self.assertEqual(output.read_bytes(),before)
            self.assertEqual(pages.daily_inventory.call_count,2)
            self.assertEqual(blog.daily_inventory.call_count,2)

    def test_evidence_rejects_invalid_shapes_empty_or_wrong_window(self):
        import weekly_source_evidence as m
        for evidence in [None, {}, {'start':'2026-10-10','end':'2026-10-16','posts':[]}, {'start':'2026-10-10','end':'2026-10-16','posts':[{'date':'2026-10-12','lang':'fr','title':'Daily'}]}]:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                m.validate_evidence('2026-10-17',evidence)
        with self.assertRaises(ValueError):
            m.validate_evidence('2026-10-16',{})

if __name__ == '__main__':
    unittest.main()
