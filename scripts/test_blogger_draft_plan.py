import importlib.util
import unittest
from pathlib import Path


class DraftPlanTest(unittest.TestCase):
    def test_new_pair_produces_read_only_plan(self):
        path = Path(__file__).with_name('blogger_draft_plan.py')
        self.assertTrue(path.exists(), 'draft-only planner is missing')
        spec = importlib.util.spec_from_file_location('blogger_draft_plan', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payloads = [
            {'title': '한국어', 'labels': ['weekly-ai-ko'], 'content': '본문'},
            {'title': 'English', 'labels': ['weekly-ai-en'], 'content': 'body'},
        ]
        plan = module.plan_drafts(payloads, [], {})
        self.assertEqual([p['action'] for p in plan], ['create', 'create'])


if __name__ == '__main__':
    unittest.main()
