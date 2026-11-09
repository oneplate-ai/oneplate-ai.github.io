import hashlib
import unittest

from blogger_draft_generic import prepare_pair


def sha(data):
    return hashlib.sha256(data).hexdigest()


def draft(lang, translation='pair'):
    path = '/posts/a/' if lang == 'ko' else '/en/posts/a/'
    return (f'---\ntitle: Title {lang}\nseries: weekly-ai\nlang: {lang}\ndate: 2026-11-07\ntranslation_key: {translation}\npermalink: {path}\n---\n<p>Hello</p>\n').encode()


class GenericDraftTests(unittest.TestCase):
    def test_pair_uses_release_formatter_and_binds_source(self):
        ko, en, images = draft('ko'), draft('en'), b'{}'
        result = prepare_pair({'ko': ko, 'en': en}, {'ko': sha(ko), 'en': sha(en)}, images, sha(images))
        self.assertEqual(result['ko']['labels'], ['weekly-ai-ko'])
        self.assertIn('&nbsp;Hello', result['ko']['content'])

    def test_mismatched_pair_rejected(self):
        ko, en, images = draft('ko'), draft('en', 'other'), b'{}'
        with self.assertRaises(ValueError):
            prepare_pair({'ko': ko, 'en': en}, {'ko': sha(ko), 'en': sha(en)}, images, sha(images))

    def test_bad_map_hash_rejected(self):
        ko, en = draft('ko'), draft('en')
        with self.assertRaises(ValueError):
            prepare_pair({'ko': ko, 'en': en}, {'ko': sha(ko), 'en': sha(en)}, b'{}', '0' * 64)


if __name__ == '__main__':
    unittest.main()
