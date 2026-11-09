"""Draft-only conditional PUT adapter; leaves the publisher transport untouched."""
import json
import re
from urllib.parse import urlencode
from urllib.request import Request
from urllib.error import HTTPError

from blogger_publish import BLOGGER_API


class GuardedTransport:
    def __init__(self, client):
        if not re.fullmatch(r'[0-9]+', client.blog_id):
            raise ValueError('invalid blog ID')
        self.client = client

    def __call__(self, method, path, query=None, body=None, headers=None):
        if method == 'GET' and headers is None:
            return self.client._request(method, path, query=query, body=body)
        if (method == 'POST' and path == '/blogs/' + self.client.blog_id + '/posts'
                and query == {'isDraft': 'true'} and isinstance(body, dict) and headers is None):
            return self.client._request(method, path, query=query, body=body)
        etag = headers.get('If-Match') if isinstance(headers, dict) and set(headers) == {'If-Match'} else None
        if (method != 'PUT' or not re.fullmatch(r'/blogs/' + self.client.blog_id + r'/posts/[0-9]+', path)
                or query != {'isDraft': 'true'} or not isinstance(body, dict)
                or not isinstance(etag, str) or not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag)):
            raise ValueError('invalid conditional draft request')
        req = Request(BLOGGER_API + path + '?' + urlencode({'isDraft': 'true'}),
                      data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + self.client.access_token,
                               'Content-Type': 'application/json', 'If-Match': etag}, method='PUT')
        try:
            with self.client.opener(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            error.close()
            raise RuntimeError('conditional draft request failed') from None
        except Exception:
            raise RuntimeError('conditional draft request failed') from None
