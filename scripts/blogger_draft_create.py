"""Create-only bilingual drafts. Lock and journals use a fixed host-local private directory.

The host-local lock cannot exclude UI or other hosts; exclude all other writers.
Any journal, including a completed one, blocks subsequent runs until an operator
reconciles and archives it. Never retry an uncertain POST automatically.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path

GENERIC_STATE_ROOT = Path.home() / ".hermes/work-records/ai-oneplate/blogger-generic-locks"
import re
import stat

from blogger_draft_update_generic import assert_no_pending
from blogger_draft_generic import prepare_pair
from blogger_draft_inventory import read_inventory
from blogger_draft_plan import digest, plan_drafts


class CreateError(RuntimeError):
    pass


def _read(path, expected):
    if not isinstance(path, str) or not os.path.isabs(path) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('invalid path or hash')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('not a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            raw = f.read()
    finally:
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('file hash mismatch')
    return raw


def _inputs(blog_id, manifest_path, manifest_hash):
    raw = _read(str(manifest_path), manifest_hash)
    manifest = json.loads(raw)
    if not isinstance(blog_id, str) or not re.fullmatch(r'[0-9]+', blog_id) or not isinstance(manifest, dict) or set(manifest) != {'blog_id', 'sources', 'image_map', 'payload_sha256'} or manifest['blog_id'] != blog_id:
        raise ValueError('invalid manifest or blog')
    sources, hashes = manifest['sources'], manifest['payload_sha256']
    if not isinstance(sources, dict) or set(sources) != {'ko', 'en'} or not isinstance(hashes, dict) or set(hashes) != {'ko', 'en'}:
        raise ValueError('invalid pair')
    def load(entry):
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
            raise ValueError('invalid file entry')
        return _read(entry['path'], entry['sha256'])
    source_bytes = {lang: load(sources[lang]) for lang in ('ko', 'en')}
    image = load(manifest['image_map'])
    payloads = prepare_pair(source_bytes, {lang: sources[lang]['sha256'] for lang in sources}, image, manifest['image_map']['sha256'])
    if any(digest(payloads[k]) != hashes[k] for k in ('ko', 'en')):
        raise ValueError('payload hash mismatch')
    return manifest, [payloads['ko'], payloads['en']]


def plan(request, blog_id, manifest_path, manifest_hash):
    """Read-only plan: no local state or remote writes."""
    _, payloads = _inputs(blog_id, manifest_path, manifest_hash)
    return plan_drafts(payloads, read_inventory(request, blog_id), {})


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(request, blog_id, manifest_path, manifest_hash, state_dir):
    """Apply once. Failure always requires manual journal/inventory reconciliation."""
    if not isinstance(blog_id, str) or not re.fullmatch(r'[0-9]+', blog_id) or not isinstance(manifest_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', manifest_hash):
        raise CreateError('invalid blog or manifest hash')
    state = GENERIC_STATE_ROOT
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sync_dir(state.parent)
    journals = state / 'journals'
    journals.mkdir(mode=0o700, exist_ok=True)
    _sync_dir(state)
    lock = os.open(state / (blog_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    completed, created, language = {}, {}, None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_no_pending(journals, blog_id)
        manifest, payloads = _inputs(blog_id, manifest_path, manifest_hash)
        fd = os.open(journals / ('create-' + manifest_hash + '.jsonl'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            _sync_dir(journals)
            def record(value):
                data = (json.dumps(value, sort_keys=True) + '\n').encode()
                while data:
                    n = os.write(fd, data)
                    if n <= 0:
                        raise OSError('journal write failed')
                    data = data[n:]
                os.fsync(fd)
            record({'event': 'intent', 'blog_id': blog_id, 'manifest_path': str(manifest_path), 'manifest_sha256': manifest_hash, 'manifest': manifest})
            before = read_inventory(request, blog_id)
            initial = plan_drafts(payloads, before, {})
            baseline = {p['id']: p for p in before}
            for index, item in enumerate(initial):
                language = item['language']
                _, fresh_payloads = _inputs(blog_id, manifest_path, manifest_hash)
                if fresh_payloads != payloads:
                    raise ValueError('inputs changed')
                current = read_inventory(request, blog_id)
                now = {p['id']: p for p in current}
                if any(now.get(k) != v for k, v in baseline.items()) or set(now) != set(baseline) | set(completed.values()):
                    raise ValueError('inventory changed')
                fresh = plan_drafts(payloads, current, completed)
                if any(row['action'] != ('none' if row['language'] in completed else 'create') or row['payload_sha256'] != initial[i]['payload_sha256'] for i, row in enumerate(fresh)):
                    raise ValueError('plan changed')
                body = payloads[index]
                record({'event': 'request', 'language': language, 'payload_sha256': digest(body)})
                response = request('POST', f'/blogs/{blog_id}/posts', query={'isDraft': 'true'}, body=body)
                post_id = response.get('id') if isinstance(response, dict) else None
                if not isinstance(post_id, str) or not re.fullmatch(r'[0-9]+', post_id):
                    raise ValueError('uncertain response')
                created[language] = post_id
                record({'event': 'response', 'language': language, 'id': post_id})
                after = read_inventory(request, blog_id)
                now = {p['id']: p for p in after}
                if any(now.get(k) != v for k, v in baseline.items()) or set(now) != set(baseline) | set(completed.values()) | {post_id}:
                    raise ValueError('unrelated inventory changed')
                for lang, prior_id in completed.items():
                    prior_body = payloads[('ko', 'en').index(lang)]
                    prior = now.get(prior_id, {})
                    if prior.get('status') != 'DRAFT' or any(prior.get(k) != prior_body[k] for k in ('title', 'content', 'labels')):
                        raise ValueError('previous draft changed')
                post = now.get(post_id, {})
                if post.get('status') != 'DRAFT' or any(post.get(k) != body[k] for k in ('title', 'content', 'labels')) or sum(p['title'] == body['title'] for p in after) != 1:
                    raise ValueError('draft readback mismatch')
                completed[language] = post_id
            record({'event': 'complete', 'ids': completed})
            return completed
        finally:
            os.close(fd)
    except Exception:
        raise CreateError(f'operation stopped; reconcile journal before retry; completed={completed}; created={created}; attempted={language}') from None
    finally:
        os.close(lock)
