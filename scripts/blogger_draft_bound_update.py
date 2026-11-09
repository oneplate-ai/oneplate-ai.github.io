"""Source-bound wrapper for the draft-only updater; not a standalone CLI.

plan(request, blog_id, ids, source_paths, image_map_path) returns a manifest;
approve manifest_hash(manifest), then pass it and the same input files to apply.
The caller must quiesce external writers. The updater enforces binding itself;
its local lock cannot exclude other hosts or the Blogger UI.
"""
import hashlib
from pathlib import Path

import blogger_draft_update_generic as update
from blogger_draft_generic import prepare_pair
from blogger_draft_inventory import read_inventory

UpdateError = update.UpdateError
manifest_hash = update.manifest_hash


def _inputs(source_paths, image_map_path):
    if not isinstance(source_paths, dict) or set(source_paths) != {'ko', 'en'}:
        raise UpdateError('exactly ko and en source paths required')
    sources = {lang: Path(source_paths[lang]).read_bytes() for lang in ('ko', 'en')}
    image = Path(image_map_path).read_bytes()
    hashes = {lang: hashlib.sha256(raw).hexdigest() for lang, raw in sources.items()}
    image_hash = hashlib.sha256(image).hexdigest()
    return hashes, image_hash, prepare_pair(sources, hashes, image, image_hash)


def plan(request, blog_id, ids, source_paths, image_map_path):
    if not isinstance(ids, dict) or set(ids) != {'ko', 'en'} or len(set(ids.values())) != 2:
        raise UpdateError('distinct ko and en IDs required')
    hashes, image_hash, payloads = _inputs(source_paths, image_map_path)
    posts = {p['id']: p for p in read_inventory(request, blog_id)}
    targets = {}
    for lang in ('ko', 'en'):
        post = posts.get(ids[lang])
        if not post or post.get('title') != payloads[lang]['title'] or post.get('labels') != payloads[lang]['labels']:
            raise UpdateError('draft title or language/series labels mismatch')
        targets[lang] = {'id': ids[lang], 'body_sha256': update.body_hash(post),
                         'metadata_sha256': update.metadata_hash(post), 'payload': payloads[lang]}
    inner = update.plan(request, blog_id, targets)
    return {'version': 1, 'sources': {lang: {'path': str(Path(source_paths[lang]).resolve()), 'sha256': hashes[lang]} for lang in ('ko', 'en')}, 'image_map': {'path': str(Path(image_map_path).resolve()), 'sha256': image_hash}, 'update': inner}


def apply(request, manifest, approved_hash, source_paths, image_map_path, state_dir):
    try:
        if any(str(Path(source_paths[lang]).resolve()) != manifest['sources'][lang]['path'] for lang in ('ko', 'en')) or str(Path(image_map_path).resolve()) != manifest['image_map']['path']:
            raise ValueError()
    except Exception:
        raise UpdateError('bound paths mismatch') from None
    return update.apply(request, manifest, approved_hash, state_dir)
