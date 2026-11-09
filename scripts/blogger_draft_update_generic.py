#!/usr/bin/env python3
"""Exact-pair, draft-only update. External writers must be quiesced.

plan() performs reads only. Persist the returned manifest and independently
approve its hash before apply(). Lock and journals use a fixed private directory;
a journal, even an incomplete one, permanently prevents automatic replay.
The local lock does not exclude the Blogger UI or writers on other hosts.
"""
import copy
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

GENERIC_STATE_ROOT = Path.home() / ".hermes/work-records/ai-oneplate/blogger-generic-locks"

from blogger_draft_inventory import read_inventory
from blogger_draft_generic import prepare_pair

class UpdateError(RuntimeError):
    pass

def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()

def body_hash(post):
    return hashlib.sha256(post['content'].encode('utf-8')).hexdigest()

def _metadata(post):
    # Blogger changes these server-generated revision fields on a successful PUT.
    return {k: copy.deepcopy(v) for k, v in post.items() if k not in ('content', 'etag', 'updated')}

def metadata_hash(post):
    return _hash(_metadata(post))

def manifest_hash(manifest):
    return _hash(manifest)

def _inventory(request, blog_id):
    try:
        return {p['id']: p for p in read_inventory(request, blog_id)}
    except Exception as exc:
        raise UpdateError('status inventory failed') from None

def _validate_manifest(m):
    if not isinstance(m, dict) or set(m) != {'version','blog_id','targets'} or m['version'] != 1 or not isinstance(m['blog_id'], str) or not re.fullmatch(r'[0-9]+', m['blog_id']):
        raise UpdateError('invalid manifest')
    targets = m['targets']
    if not isinstance(targets, dict) or set(targets) != {'ko','en'}:
        raise UpdateError('exactly ko and en required')
    ids = []
    for lang in ('ko','en'):
        item = targets[lang]
        if not isinstance(item, dict) or set(item) != {'id','body_sha256','metadata_sha256','payload'}:
            raise UpdateError('invalid target')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[0-9]+', item['id']):
            raise UpdateError('invalid ID')
        ids.append(item['id'])
        if any(not isinstance(item[k], str) or not re.fullmatch(r'[0-9a-f]{64}', item[k]) for k in ('body_sha256','metadata_sha256')):
            raise UpdateError('invalid expected hash')
        payload = item['payload']
        if not isinstance(payload, dict) or set(payload) != {'title','content','labels'} or not isinstance(payload['title'], str) or not payload['title'] or not isinstance(payload['content'], str) or not isinstance(payload['labels'], list) or any(not isinstance(x, str) for x in payload['labels']):
            raise UpdateError('invalid payload')
    if len(set(ids)) != 2:
        raise UpdateError('duplicate target ID')
    if len({targets[lang]['payload']['title'] for lang in ('ko', 'en')}) != 2:
        raise UpdateError('duplicate target title')

def _check_titles(posts, targets):
    for target in targets.values():
        matches = [post['id'] for post in posts.values() if post['title'] == target['payload']['title']]
        if matches != [target['id']]:
            raise UpdateError('target title collision')

def _before(post, target, blog_id):
    if post is None or post.get('id') != target['id'] or post.get('status') != 'DRAFT' or post.get('blog', {}).get('id') != blog_id or body_hash(post) != target['body_sha256'] or metadata_hash(post) != target['metadata_sha256']:
        raise UpdateError('draft identity or precondition changed')

def _after(post, target, blog_id, original_metadata):
    if not isinstance(post, dict) or post.get('id') != target['id'] or post.get('status') != 'DRAFT' or post.get('blog', {}).get('id') != blog_id:
        raise UpdateError('invalid update response')
    expected = copy.deepcopy(original_metadata)
    expected.update({k: copy.deepcopy(target['payload'][k]) for k in ('title','labels')})
    if _metadata(post) != expected or post.get('content') != target['payload']['content']:
        raise UpdateError('update response or readback mismatch')

def plan(request, blog_id, targets):
    manifest = {'version': 1, 'blog_id': blog_id, 'targets': copy.deepcopy(targets)}
    _validate_manifest(manifest)
    posts = _inventory(request, blog_id)
    _check_titles(posts, manifest['targets'])
    for target in manifest['targets'].values():
        _before(posts.get(target['id']), target, blog_id)
    return manifest

def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)

def assert_no_pending(journals, blog_id):
    # Ambiguous/corrupt journals block all blogs; operator must reconcile manually.
    for path in journals.iterdir():
        if not path.name.endswith('.jsonl'):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError('invalid journal')
            with path.open(encoding='utf-8') as stream:
                first = json.loads(stream.readline())
            stored = first.get('manifest', {})
            journal_blog = first.get('blog_id') or stored.get('blog_id') or stored.get('update', {}).get('blog_id')
            if not isinstance(journal_blog, str) or not re.fullmatch(r'[0-9]+', journal_blog):
                raise ValueError('missing blog')
        except Exception:
            raise UpdateError('unreadable journal; reconcile manually') from None
        if journal_blog == blog_id:
            raise UpdateError('existing blog journal; reconcile manually')

def _published(post):
    value = post.get('published')
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise UpdateError('missing or invalid published timestamp')
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise UpdateError('missing or invalid published timestamp') from None
    return value

def apply(request, manifest, approved_hash, state_dir):
    manifest = copy.deepcopy(manifest)
    if not isinstance(approved_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', approved_hash) or manifest_hash(manifest) != approved_hash:
        raise UpdateError('manifest approval hash mismatch')
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'sources', 'image_map', 'update'} or manifest['version'] != 1:
        raise UpdateError('source-bound manifest required')
    bound = manifest
    manifest = bound['update']
    _validate_manifest(manifest)
    def check_binding():
        try:
            sources = bound['sources']; image = bound['image_map']
            if not isinstance(sources, dict) or set(sources) != {'ko', 'en'}:
                raise ValueError()
            entries = [sources['ko'], sources['en'], image]
            if any(not isinstance(x, dict) or set(x) != {'path', 'sha256'} or not isinstance(x['path'], str) or not Path(x['path']).is_absolute() for x in entries):
                raise ValueError()
            raw = {lang: Path(sources[lang]['path']).read_bytes() for lang in ('ko', 'en')}
            image_bytes = Path(image['path']).read_bytes()
            payloads = prepare_pair(raw, {lang: sources[lang]['sha256'] for lang in raw}, image_bytes, image['sha256'])
            if any(payloads[lang] != manifest['targets'][lang]['payload'] for lang in ('ko', 'en')):
                raise ValueError()
        except Exception:
            raise UpdateError('source, image map or payload mismatch') from None
    def check_identity(posts):
        _check_titles(posts, manifest['targets'])
        for lang, target in manifest['targets'].items():
            post = posts.get(target['id'])
            if not post or post.get('status') != 'DRAFT' or post.get('blog', {}).get('id') != manifest['blog_id'] or post.get('title') != target['payload']['title'] or post.get('labels') != target['payload']['labels']:
                raise UpdateError('draft title or language/series labels mismatch')
    check_binding()
    state = GENERIC_STATE_ROOT
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sync_dir(state.parent)
    journals = state / 'journals'
    journals.mkdir(mode=0o700, exist_ok=True)
    _sync_dir(state)
    lock = os.open(state / (manifest['blog_id'] + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise UpdateError('operation already running') from None
        assert_no_pending(journals, manifest['blog_id'])
        try: fd = os.open(journals / ('update-' + approved_hash + '.jsonl'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError: raise UpdateError('existing journal: reconcile manually, never replay') from None
        try:
            _sync_dir(journals)
            def record(event):
                data = (json.dumps(event, sort_keys=True, ensure_ascii=False) + '\n').encode()
                while data:
                    n = os.write(fd, data)
                    if n <= 0: raise OSError('journal write failed')
                    data = data[n:]
                os.fsync(fd)
            record({'event':'intent','manifest_sha256':approved_hash, 'manifest':bound})
            targets = manifest['targets']; blog_id = manifest['blog_id']
            attempted = False
            try:
                posts = _inventory(request, blog_id)
                for target in targets.values(): _before(posts.get(target['id']), target, blog_id)
                check_identity(posts)
                dates = {lang: _published(posts[t['id']]) for lang, t in targets.items()}
                record({'event':'backup', 'posts':{lang:copy.deepcopy(posts[t['id']]) for lang,t in targets.items()}})
                original = {lang:_metadata(posts[t['id']]) for lang,t in targets.items()}
                for lang in ('ko','en'):
                    target = targets[lang]
                    posts = _inventory(request, blog_id)
                    _before(posts.get(target['id']), target, blog_id)
                    check_identity(posts)
                    check_binding()
                    etag = posts[target['id']].get('etag')
                    if not isinstance(etag, str) or not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag):
                        raise UpdateError('missing or invalid strong ETag')
                    payload = copy.deepcopy(target['payload'])
                    payload['published'] = dates[lang]
                    record({'event':'request','id':target['id'], 'payload_sha256':_hash(payload), 'etag':etag})
                    attempted = True
                    response = request('PUT', f"/blogs/{blog_id}/posts/{target['id']}", query={'isDraft':'true'}, body=payload, headers={'If-Match': etag})
                    _after(response, target, blog_id, original[lang])
                    _after(_inventory(request, blog_id).get(target['id']), target, blog_id, original[lang])
                    record({'event':'verified','id':target['id']})
                posts = _inventory(request, blog_id)
                for lang,t in targets.items(): _after(posts.get(t['id']), t, blog_id, original[lang])
                record({'event':'complete'})
                return {lang:t['id'] for lang,t in targets.items()}
            except Exception as exc:
                if attempted:
                    try:
                        posts = _inventory(request, blog_id)
                        for t in targets.values():
                            p = posts.get(t['id'])
                            record({'event':'failure-readback','id':t['id'],'status':p.get('status') if p else None,'body_sha256':body_hash(p) if p else None,'metadata_sha256':metadata_hash(p) if p else None})
                    except Exception:
                        record({'event':'failure-readback-unavailable'})
                raise UpdateError('stopped; inspect journal and reconcile manually') from None
        finally: os.close(fd)
    finally: os.close(lock)
