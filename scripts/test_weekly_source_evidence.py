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
        evidence = {'schema_version':1, 'source_channel':'blogger', 'start':'2026-10-10','end':'2026-10-16','posts':[{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]}
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
        from publication_gate import validate_approval, GateError
        for invalid in [{**evidence, 'source_channel':'pages'}, {**evidence, 'posts':evidence['posts'][:1]}, {**evidence, 'schema_version':True}]:
            record = json.loads(output.read_text())
            record['weekly_evidence'] = invalid
            output.write_text(json.dumps(record))
            with self.subTest(invalid=invalid), self.assertRaises(GateError):
                validate_approval(output, fixture.root, 'weekly-ai', '2026-10-17')

    def test_blogger_evidence_has_explicit_scope_and_never_uses_dual_guard(self):
        import weekly_source_evidence as m
        from unittest.mock import patch
        rows = [{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]
        self.assertTrue(hasattr(m, 'build_blogger_evidence'), 'Blogger-only evidence builder missing')
        with patch('dual_channel_release.check_weekly_evidence', side_effect=AssertionError('no fabricated parity')):
            evidence = m.build_blogger_evidence('2026-10-17', rows)
            self.assertEqual(evidence['source_channel'], 'blogger')
            self.assertEqual(evidence['schema_version'], 1)
            self.assertEqual(m.validate_evidence('2026-10-17', evidence), evidence)
        for bad in [rows[:1], rows + rows, [], [{**rows[0], 'date':'2026-10-09'}, rows[1]]]:
            with self.subTest(rows=bad), self.assertRaises(ValueError):
                m.build_blogger_evidence('2026-10-17', bad)

    def test_export_cli_reads_blogger_only_and_never_overwrites_reviewed_evidence(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, Mock
        import weekly_source_evidence as m
        rows = [{'date':'2026-10-12','lang':'ko','title':'일간'}, {'date':'2026-10-12','lang':'en','title':'Daily'}]
        pages, blog = Mock(), Mock()
        pages.daily_inventory.return_value = rows
        blog.daily_inventory.return_value = rows
        with tempfile.TemporaryDirectory() as tmp, patch('dual_channel_release.PagesChannel', side_effect=AssertionError('Pages forbidden')), patch.object(m, 'BloggerChannel', return_value=blog):
            output = Path(tmp)/'evidence.json'
            self.assertEqual(m.main(['--date','2026-10-17','--output',str(output)]),0)
            before = output.read_bytes()
            self.assertEqual(len(json.loads(before)['posts']),2)
            self.assertEqual(m.main(['--date','2026-10-17','--output',str(output)]),1)
            self.assertEqual(output.read_bytes(),before)
            pages.daily_inventory.assert_not_called()
            self.assertEqual(json.loads(before)['source_channel'], 'blogger')
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
