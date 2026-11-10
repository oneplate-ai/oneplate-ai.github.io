import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import blogger_draft_reconcile as module


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journals = self.root / 'journals'
        self.journals.mkdir()
        self.h = 'a' * 64
        self.manifest = {'blog_id': '123', 'sources': {lang: {'path': '/' + lang, 'sha256': 'b' * 64} for lang in ('ko', 'en')}}
        self.payloads = [
            {'title': 'ko', 'content': '<p>ko</p>', 'labels': ['today-ai-bite-ko']},
            {'title': 'en', 'content': '<p>en</p>', 'labels': ['today-ai-bite-en']},
        ]
        self.ids = {'ko': '1', 'en': '2'}
        self.records = [
            {'event': 'intent', 'blog_id': '123', 'manifest_path': '/manifest', 'manifest_sha256': self.h, 'manifest': self.manifest},
            {'event': 'request', 'language': 'ko', 'payload_sha256': module.digest(self.payloads[0])},
            {'event': 'response', 'language': 'ko', 'id': '1'},
            {'event': 'request', 'language': 'en', 'payload_sha256': module.digest(self.payloads[1])},
            {'event': 'response', 'language': 'en', 'id': '2'},
            {'event': 'complete', 'ids': self.ids},
        ]
        self.inventory = [dict(payload, id=self.ids[lang], status='DRAFT') for lang, payload in zip(('ko', 'en'), self.payloads)]
        self.path = self.journals / ('create-' + self.h + '.jsonl')

    def write_records(self):
        self.path.write_text(''.join(json.dumps(x) + '\n' for x in self.records))

    def call(self):
        with patch.object(module.create, 'GENERIC_STATE_ROOT', self.root), \
             patch.object(module.create, '_inputs', return_value=(self.manifest, self.payloads)), \
             patch.object(module.create, '_read', side_effect=lambda p, h: ('---\nseries: today-ai-bite\ndate: 2026-11-11\nlang: ' + p[1:] + '\n---\n').encode()), \
             patch.object(module, 'read_inventory', return_value=self.inventory):
            return module.reconcile(None, '123', '/manifest', self.h, self.ids)

    def test_complete_exact_pair_archives_without_deleting(self):
        self.write_records()
        result = self.call()
        self.assertEqual(result['ids'], self.ids)
        binding = json.loads((self.root / 'bindings' / '2026-11-11-today-ai-bite.json').read_text())
        self.assertEqual(binding['ids'], self.ids)
        self.assertFalse(self.path.exists())
        self.assertEqual(len(list((self.root / 'archive').iterdir())), 1)

    def test_incomplete_journal_stays_active(self):
        self.records.pop()
        self.write_records()
        with self.assertRaises(module.ReconcileError): self.call()
        self.assertTrue(self.path.exists())

    def test_remote_edited_draft_stays_active(self):
        self.inventory[0]['content'] = 'edited'
        self.write_records()
        with self.assertRaises(module.ReconcileError): self.call()
        self.assertTrue(self.path.exists())

    def test_archive_collision_does_not_replace(self):
        self.write_records()
        archive = self.root / 'archive'
        archive.mkdir()
        dest = archive / self.path.name
        dest.write_text('preserve')
        with self.assertRaises(module.ReconcileError): self.call()
        self.assertEqual(dest.read_text(), 'preserve')
        self.assertTrue(self.path.exists())
