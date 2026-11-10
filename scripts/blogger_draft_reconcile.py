"""Reconcile a complete create journal against current drafts before archival.

No Blogger writes. The host-local lock cannot exclude the UI or other hosts;
all other writers must remain quiescent until reconciliation finishes.
"""
import fcntl
import json
import os
import re
from pathlib import Path
from datetime import date
import stat

from blogger_adapter import _front_matter_and_body

import blogger_draft_create as create
from blogger_draft_inventory import read_inventory
from blogger_draft_plan import digest, plan_drafts


class ReconcileError(RuntimeError):
    pass


def reconcile(request, blog_id, manifest_path, manifest_hash, ids):
    try:
        if not re.fullmatch(r'[0-9]+', blog_id) or not re.fullmatch(r'[0-9a-f]{64}', manifest_hash):
            raise ValueError('invalid identifier')
        if set(ids) != {'ko', 'en'} or any(not re.fullmatch(r'[0-9]+', v) for v in ids.values()) or ids['ko'] == ids['en']:
            raise ValueError('invalid pair IDs')
        state = create.GENERIC_STATE_ROOT
        journals = state / 'journals'
        archive = state / 'archive'
        lock = os.open(state / (blog_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest, payloads = create._inputs(blog_id, manifest_path, manifest_hash)
            journal = journals / ('create-' + manifest_hash + '.jsonl')
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('journal is not regular')
                records = [json.loads(line) for line in stream]
            expected = [
                {'event': 'intent', 'blog_id': blog_id, 'manifest_path': str(manifest_path), 'manifest_sha256': manifest_hash, 'manifest': manifest},
                {'event': 'request', 'language': 'ko', 'payload_sha256': digest(payloads[0])},
                {'event': 'response', 'language': 'ko', 'id': ids['ko']},
                {'event': 'request', 'language': 'en', 'payload_sha256': digest(payloads[1])},
                {'event': 'response', 'language': 'en', 'id': ids['en']},
                {'event': 'complete', 'ids': ids},
            ]
            if records != expected:
                raise ValueError('incomplete or unexpected journal')
            # Any other journal, including one with unknown identity, remains a blocker.
            for other in journals.iterdir():
                if other == journal or not other.name.endswith('.jsonl'):
                    continue
                if other.is_symlink() or not other.is_file():
                    raise ValueError('invalid journal')
                with other.open(encoding='utf-8') as stream:
                    first = json.loads(stream.readline())
                stored = first.get('manifest', {})
                other_blog = first.get('blog_id') or stored.get('blog_id') or stored.get('update', {}).get('blog_id')
                if not isinstance(other_blog, str) or not re.fullmatch(r'[0-9]+', other_blog) or other_blog == blog_id:
                    raise ValueError('another journal requires reconciliation')
            plan = plan_drafts(payloads, read_inventory(request, blog_id), ids)
            if any(item['action'] != 'none' for item in plan):
                raise ValueError('remote draft differs')
            metadata = []
            for lang in ('ko', 'en'):
                entry = manifest['sources'][lang]
                meta, _ = _front_matter_and_body(create._read(entry['path'], entry['sha256']).decode('utf-8'))
                if meta.get('lang') != lang:
                    raise ValueError('source language mismatch')
                metadata.append(meta)
            day, series = str(metadata[0].get('date')), str(metadata[0].get('series'))
            if (series not in {'today-ai-bite', 'weekly-ai'} or date.fromisoformat(day).isoformat() != day
                    or any(str(m.get('date')) != day or m.get('series') != series for m in metadata)):
                raise ValueError('source release identity mismatch')
            bindings = state / 'bindings'
            bindings.mkdir(mode=0o700, exist_ok=True)
            if bindings.is_symlink() or not bindings.is_dir():
                raise ValueError('invalid binding directory')
            binding = bindings / (day + '-' + series + '.json')
            data = (json.dumps({'version': 1, 'blog_id': blog_id, 'day': day, 'series': series,
                                'ids': ids, 'manifest_sha256': manifest_hash}, sort_keys=True) + '\n').encode()
            try:
                bind_fd = os.open(binding, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                bind_fd = os.open(binding, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(bind_fd, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read() != data:
                        raise ValueError('binding conflict')
            else:
                with os.fdopen(bind_fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                create._sync_dir(bindings)
            archive.mkdir(mode=0o700, exist_ok=True)
            if archive.is_symlink() or not archive.is_dir():
                raise ValueError('invalid archive')
            destination = archive / journal.name
            # Link refuses an existing destination; do not overwrite evidence.
            os.link(journal, destination, follow_symlinks=False)
            create._sync_dir(archive)
            os.unlink(journal)
            create._sync_dir(journals)
            return {'ids': ids, 'archive': str(destination)}
        finally:
            os.close(lock)
    except Exception:
        raise ReconcileError('reconciliation stopped; preserve journal and inspect remote state before retry') from None
