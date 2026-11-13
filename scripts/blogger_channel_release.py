#!/usr/bin/env python3
"""Blogger-only bilingual publication; default is a read-only plan.

Apply requires an externally established single-writer window. The shared host
lock does not exclude Blogger UI or writers on other hosts. Every attempt journal
(including success) blocks replay until an operator reconciles and archives it.
No Pages API, website, repository build, or public-file materialization is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

import dual_channel_release as base
from dual_channel_release import load_unit, ReleaseError
from blogger_draft_update_generic import assert_no_pending


class StrictReleaseClient(base.blogger.ReleaseClient):
    """Reject malformed/repeated status pages rather than infer an empty set."""
    def list_posts(self, state):
        if state not in {'LIVE', 'DRAFT', 'SCHEDULED'}:
            raise ReleaseError('invalid inventory status')
        result, ids, tokens, token = [], set(), set(), None
        while True:
            query = {'fetchDrafts': 'true', 'status': state.lower(),
                     'view': 'ADMIN', 'fetchBodies': 'true', 'maxResults': '100'}
            if token is not None:
                query['pageToken'] = token
            page = self.client._request('GET', f'/blogs/{self.client.blog_id}/posts', query=query)
            if (not isinstance(page, dict) or page.get('kind') != 'blogger#postList'
                    or not isinstance(page.get('etag'), str) or not page['etag']
                    or not isinstance(page.get('items', []), list)):
                raise ReleaseError('invalid inventory response; outcome unknown')
            for post in page.get('items', []):
                if (not isinstance(post, dict) or post.get('kind') != 'blogger#post'
                        or not isinstance(post.get('id'), str) or not re.fullmatch(r'[0-9]+', post['id'])
                        or post['id'] in ids or post.get('status') != state
                        or not isinstance(post.get('blog'), dict) or post['blog'].get('id') != str(self.client.blog_id)
                        or not isinstance(post.get('title'), str) or not post['title']
                        or not isinstance(post.get('content'), str)
                        or not isinstance(post.get('labels', []), list)
                        or any(not isinstance(x, str) for x in post.get('labels', []))):
                    raise ReleaseError('invalid or duplicate inventory item; outcome unknown')
                ids.add(post['id'])
                result.append(post)
            token = page.get('nextPageToken')
            if token is None:
                return result
            if not isinstance(token, str) or not token or token in tokens:
                raise ReleaseError('invalid/repeated inventory page token')
            tokens.add(token)

    def current(self, allowed_keys=None):
        posts = [(state, post) for state in ('LIVE', 'DRAFT', 'SCHEDULED') for post in self.list_posts(state)]
        ids = [post['id'] for _, post in posts]
        if len(ids) != len(set(ids)):
            raise ReleaseError('inconsistent all-state inventory')
        if allowed_keys is not None:
            for title, lang in allowed_keys:
                hits = [post for _, post in posts if post['title'] == title]
                if len(hits) > 1 or any(base.blogger.label_identity(p.get('labels', [])) is None
                                        or base.blogger.label_identity(p.get('labels', []))[1] != lang for p in hits):
                    raise base.blogger.DraftConflictError('raw title collision across Blogger states')
        current = {}
        for state in ('LIVE', 'DRAFT', 'SCHEDULED'):
            indexed = base.blogger.index_posts([p for s, p in posts if s == state], state, allowed_keys=allowed_keys)
            if set(current) & set(indexed):
                raise ReleaseError('duplicate Blogger key across states')
            current.update(indexed)
        return current


class JournaledBloggerClient(base.GuardedBloggerClient):
    """Reuse exact-payload/ID guards; journal every mutation before/after send."""
    recorder = None

    def authorize(self, plan):
        super().authorize(plan)
        self.targets = {i['id']: tuple(i['key']) for i in plan['inventory']}
        self.revisions = {i['id']: i['sha256'] for i in plan['inventory']}
        self.promotions = {i['id'] for i in plan['writes'] if i['action'] == 'publish_draft'}
        self.display_updates = {i['id'] for i in plan['writes'] if i['action'] == 'remove_language_switcher'}

    def _request(self, method, path, *, query=None, body=None):
        if method == 'GET':
            return super()._request(method, path, query=query, body=body)
        if self.recorder is None:
            raise ReleaseError('durable publication journal required')
        self.unit.verify_frozen()
        base.blogger.require_immediate_publish_time(self.unit.approval['scheduled_publish_at'])
        current = StrictReleaseClient(self).current(set(self.unit.sources))
        weekly_guard(self.unit, BloggerChannel(blog_id=self.blog_id,
                     client_factory=lambda unit: StrictReleaseClient(self)))
        changes = base.blogger.plan_release(self.unit.sources, current)
        if any(c['action'] == 'update_live' for c in changes):
            raise ReleaseError('Blogger LIVE conflict before mutation')
        # A newly appearing same-title post must not be duplicated by a create.
        if path == f'/blogs/{self.blog_id}/posts' and body is not None:
            identity = base.blogger.label_identity(body.get('labels', []))
            key = (body.get('title'), identity[1] if identity else '')
            if key in current:
                raise ReleaseError('Blogger create target appeared after plan')
        match = re.fullmatch(r'.*/posts/([0-9]+)(?:/publish)?', path)
        if match:
            post_id = match[1]
            selected = current.get(self.targets.get(post_id))
            if (selected is None or selected[1]['id'] != post_id
                    or base.fingerprint(selected[1]) != self.revisions.get(post_id)):
                raise ReleaseError('Blogger exact target ID/revision changed before mutation')
            if method == 'POST' and (post_id not in self.promotions or selected[0] != 'DRAFT'
                                     or query != {'publishDate': self.unit.approval['scheduled_publish_at']}):
                raise ReleaseError('unplanned Blogger promotion or publication time')
            if method == 'PUT':
                expected = base.blogger.build_client_payload({
                    'title': selected[1]['title'], 'labels': selected[1].get('labels', []),
                    'content': base.blogger.remove_language_switcher(selected[1]['content'])})
                if post_id not in self.display_updates or selected[0] != 'LIVE' or body != expected or query is not None:
                    raise ReleaseError('unplanned Blogger display-only update')
        event = {'method': method, 'path': path, 'id': match[1] if match else None}
        self.recorder({'event': 'mutation_request', **event})
        self.progress['uncertain'] = event
        try:
            result = super()._request(method, path, query=query, body=body)
        except Exception:
            self.recorder({'event': 'mutation_outcome_unknown', **event})
            raise
        post_id = result.get('id') if isinstance(result, dict) else None
        # Record even a malformed response before failing closed.
        self.recorder({'event': 'mutation_response', **event,
                       'response_id': post_id if isinstance(post_id, str) and post_id.isdigit() else None})
        if not isinstance(post_id, str) or not re.fullmatch(r'[0-9]+', post_id) or (match and post_id != match[1]):
            raise ReleaseError('invalid mutation response ID; outcome unknown')
        if not match:
            identity = base.blogger.label_identity(body.get('labels', []))
            self.targets[post_id] = (body['title'], identity[1])
            self.promotions.add(post_id)
        elif method == 'POST':
            self.promotions.discard(post_id)
        elif method == 'PUT':
            self.display_updates.discard(post_id)
        self.revisions[post_id] = base.fingerprint(result)
        self.progress.pop('uncertain', None)
        return result


class BloggerChannel(base.BloggerChannel):
    def new_client(self, unit):
        token = base.blogger.fresh_access_token(self.token_path, self.client_secret_path)
        return StrictReleaseClient(JournaledBloggerClient(token, self.blog_id, unit, self.progress))

    def apply(self, unit, files, plan):
        if not isinstance(self.release.client, JournaledBloggerClient):
            raise ReleaseError('journaled guarded client required')
        self.release.client.recorder = self.recorder
        return super().apply(unit, files, plan)


def weekly_guard(unit, channel):
    if unit.series != 'weekly-ai':
        return
    from weekly_source_evidence import validate_evidence, build_blogger_evidence
    bound = validate_evidence(unit.day, unit.approval.get('weekly_evidence'))
    if bound.get('source_channel') != 'blogger':
        raise ReleaseError('Blogger-only weekly evidence must be explicitly bound; new Preview/approval required')
    start, end = base.weekly_window(unit.day)
    actual = build_blogger_evidence(unit.day, channel.daily_inventory(unit, start, end))
    if bound != actual:
        raise ReleaseError('Blogger LIVE weekly evidence differs from approved inventory')


@contextmanager
def publication_lock(blog_id):
    if not isinstance(blog_id, str) or not re.fullmatch(r'[0-9]+', blog_id):
        raise ReleaseError('invalid blog ID')
    state = base.draft_create.GENERIC_STATE_ROOT
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.is_symlink() or not state.is_dir():
        raise ReleaseError('invalid publication state directory')
    journals = state / 'journals'
    if journals.is_symlink():
        raise ReleaseError('invalid publication journal directory')
    journals.mkdir(mode=0o700, exist_ok=True)
    if not journals.is_dir():
        raise ReleaseError('invalid publication journal directory')
    base.draft_create._sync_dir(state.parent)
    base.draft_create._sync_dir(state)
    lock = os.open(state / (blog_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_no_pending(journals, blog_id)
        yield journals
    finally:
        os.close(lock)


@contextmanager
def publication_journal(journals, unit, blog_id, plan):
    path = journals / ('publish-' + uuid.uuid4().hex + '.jsonl')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        base.draft_create._sync_dir(journals)
        def record(event):
            data = (json.dumps(event, sort_keys=True) + '\n').encode()
            while data:
                count = os.write(fd, data)
                if count <= 0:
                    raise OSError('publication journal write failed')
                data = data[count:]
            os.fsync(fd)
        record({'event': 'intent', 'blog_id': blog_id,
                'publication_unit': unit.approval['publication_unit'],
                'approval_sha256': base.fingerprint(unit.approval), 'plan': plan})
        yield record
    finally:
        os.close(fd)


def coordinate(unit, channel, *, apply=False):
    """Re-inventory only Blogger; stop and retain all progress on any failure."""
    result = {'status': 'failed', 'mode': 'apply' if apply else 'plan',
              'publication_unit': unit.approval['publication_unit'],
              'channels': {'blogger': {'status': 'failed', 'stage': 'preflight', 'completed': {}}}}
    detail = result['channels']['blogger']
    def execute(journals=None):
        unit.verify_frozen()
        weekly_guard(unit, channel)
        detail['stage'] = 'plan'
        plan = deepcopy(channel.plan(unit, {}))
        detail['plan'] = plan
        if not apply:
            detail['status'] = result['status'] = 'planned'
            return
        detail['stage'] = 'replan'
        unit.verify_frozen()
        weekly_guard(unit, channel)
        if channel.plan(unit, {}) != plan:
            raise ReleaseError('Blogger plan drift; no mutation applied')
        unit.verify_frozen()
        if plan['writes']:
            with publication_journal(journals, unit, str(channel.blog_id), plan) as record:
                channel.recorder = record
                detail['stage'] = 'apply'
                detail['completed'] = channel.apply(unit, {}, plan)
                detail['stage'] = 'verify'
                channel.verify(unit, {}, detail['completed'])
                unit.verify_frozen()
                record({'event': 'verified', 'blog_id': str(channel.blog_id)})
        else:
            detail['stage'] = 'verify'
            channel.verify(unit, {}, {})
        unit.verify_frozen()
        detail['status'] = result['status'] = 'published_verified'
    try:
        if apply:
            # No directory, journal, token refresh, or API read before this guard.
            base.blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
            with publication_lock(str(channel.blog_id)) as journals:
                execute(journals)
        else:
            execute()
    except Exception as error:
        if detail['stage'] in {'apply', 'verify'}:
            detail['completed'] = {**deepcopy(channel.progress), **detail['completed']}
            detail['error'] = 'Publication may be partial or unknown; do not retry before journal and exact-ID reconciliation.'
            detail['error_type'] = type(error).__name__
        else:
            detail['error'] = str(error)
    finally:
        # A factory must never reuse a closed recorder for a subsequent attempt.
        if apply:
            channel.recorder = None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--series', choices=('today-ai-bite', 'weekly-ai', 'easy-ai'), required=True)
    parser.add_argument('--date', required=True, dest='day')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--image-urls', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--blog-id', default=base.blogger.DEFAULT_BLOG_ID)
    parser.add_argument('--token-path', type=Path, default=base.blogger.DEFAULT_TOKEN_PATH)
    parser.add_argument('--client-secret-path', type=Path, default=base.blogger.DEFAULT_CLIENT_SECRET_PATH)
    args = parser.parse_args(argv)
    try:
        unit = load_unit(args.root, args.series, args.day, args.approval, args.image_urls)
        if args.apply:
            base.blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
        channel = BloggerChannel(blog_id=args.blog_id, token_path=args.token_path,
                                 client_secret_path=args.client_secret_path)
        result = coordinate(unit, channel, apply=args.apply)
    except Exception as error:
        result = {'status': 'blocked', 'mode': 'apply' if args.apply else 'plan', 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in {'planned', 'published_verified'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
