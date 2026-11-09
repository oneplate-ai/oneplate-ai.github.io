#!/usr/bin/env python3
"""Read-only, status-explicit Blogger inventory; no mutation entry points."""
import re


class InventoryError(ValueError):
    pass


def read_inventory(request, blog_id):
    if not isinstance(blog_id, str) or not re.fullmatch(r'[0-9]+', blog_id):
        raise InventoryError('invalid blog ID')
    posts, ids = [], set()
    for status in ('live', 'draft', 'scheduled'):
        tokens = set()
        token = None
        while True:
            query = {'status': status, 'view': 'ADMIN', 'fetchBodies': 'true', 'maxResults': '50'}
            if token is not None:
                query['pageToken'] = token
            try:
                result = request('GET', f'/blogs/{blog_id}/posts', query=query)
            except Exception:
                raise InventoryError('inventory request failed') from None
            if (not isinstance(result, dict) or result.get('kind') != 'blogger#postList'
                or not isinstance(result.get('etag'), str) or not result['etag']
                or not isinstance(result.get('items', []), list)):
                raise InventoryError('invalid inventory response')
            for post in result.get('items', []):
                if (not isinstance(post, dict) or post.get('kind') != 'blogger#post'
                    or not isinstance(post.get('title'), str) or not post['title']
                    or not isinstance(post.get('content'), str)
                    or not isinstance(post.get('labels', []), list)
                    or any(not isinstance(label, str) for label in post.get('labels', []))
                    or not isinstance(post.get('blog'), dict) or post['blog'].get('id') != blog_id
                    or post.get('status') != status.upper()
                    or not isinstance(post.get('id'), str) or not re.fullmatch(r'[0-9]+', post['id'])
                    or post['id'] in ids):
                    raise InventoryError('invalid, duplicated, or inconsistent post')
                ids.add(post['id'])
                posts.append(post)
            token = result.get('nextPageToken')
            if token is None:
                break
            if not isinstance(token, str) or not token or token in tokens:
                raise InventoryError('invalid or repeated page token')
            tokens.add(token)
    return posts
