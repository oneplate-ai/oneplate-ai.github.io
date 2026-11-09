import unittest

from blogger_draft_inventory import read_inventory, InventoryError


class InventoryTests(unittest.TestCase):
    def test_reads_all_statuses_and_pages_without_writes(self):
        calls = []
        def request(method, path, *, query):
            calls.append((method, path, dict(query)))
            status = query['status']
            if status == 'draft' and 'pageToken' not in query:
                post_id, token = '1', 'next'
            else:
                post_id, token = ('2' if status == 'draft' else '3' if status == 'live' else '4'), None
            result = {'kind': 'blogger#postList', 'etag': 'fixture', 'items': [{'id': post_id, 'status': status.upper(), 'kind': 'blogger#post', 'blog': {'id': '123'}, 'title': 'title', 'content': 'body'}]}
            if token:
                result['nextPageToken'] = token
            return result
        posts = read_inventory(request, '123')
        self.assertEqual({p['id'] for p in posts}, {'1', '2', '3', '4'})
        self.assertTrue(all(c[0] == 'GET' and c[2]['view'] == 'ADMIN' for c in calls))
        self.assertEqual(len(calls), 4)

    def test_rejects_status_mismatch_and_repeated_page(self):
        with self.assertRaises(InventoryError):
            read_inventory(lambda *a, **k: {'items': [{'id': '1', 'status': 'LIVE'}]}, '1')
        with self.assertRaises(InventoryError):
            read_inventory(lambda *a, **k: {'nextPageToken': 'loop'}, '1')

    def test_rejects_invalid_blog_id_and_malformed_items(self):
        with self.assertRaises(InventoryError):
            read_inventory(lambda *a, **k: {}, '../1')
        with self.assertRaises(InventoryError):
            read_inventory(lambda *a, **k: {'items': None}, '1')
