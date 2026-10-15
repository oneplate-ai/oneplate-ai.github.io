#!/usr/bin/env python3
import unittest

from blogger_migrate_combined_labels import LabelMigrationError, desired_labels, plan


class BloggerCombinedLabelMigrationTests(unittest.TestCase):
    def test_replaces_legacy_public_labels_with_one_combined_label(self):
        post = {"id": "1", "title": "글", "labels": ["today-ai-bite", "ko"]}
        self.assertEqual(desired_labels(post), ["today-ai-bite-ko"])
        self.assertEqual(plan([post]), [{
            "id": "1",
            "title": "글",
            "from_labels": ["ko", "today-ai-bite"],
            "to_labels": ["today-ai-bite-ko"],
        }])

    def test_keeps_non_public_management_labels(self):
        post = {"id": "2", "title": "관리 글", "labels": ["easy-ai", "en", "media-staging"]}
        self.assertEqual(desired_labels(post), ["easy-ai-en", "media-staging"])

    def test_rejects_ambiguous_public_identity(self):
        with self.assertRaises(LabelMigrationError):
            desired_labels({"id": "3", "labels": ["ko", "easy-ai", "weekly-ai"]})


if __name__ == "__main__":
    unittest.main()
