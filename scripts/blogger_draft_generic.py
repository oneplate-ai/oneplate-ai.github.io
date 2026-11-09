"""Offline draft payload preparation only; this module cannot call Blogger."""
import hashlib
import re

from blogger_adapter import _front_matter_and_body, build_blogger_payload, blogger_request_body, format_blogger_publication_content
from blogger_release import _image_urls_bytes, apply_image_mapping, ensure_image_mapping, pages_image_sources, public_copy_from_draft


def prepare_pair(sources, source_hashes, image_map, image_map_hash):
    """Prepare a hash-bound bilingual pair without creating publication approval."""
    if set(sources) != {'ko', 'en'} or set(source_hashes) != {'ko', 'en'}:
        raise ValueError('exactly ko and en sources required')
    if not isinstance(image_map, bytes) or not re.fullmatch(r'[0-9a-f]{64}', image_map_hash) or hashlib.sha256(image_map).hexdigest() != image_map_hash:
        raise ValueError('image map hash mismatch')
    mapping = _image_urls_bytes(image_map)
    result, metadata = {}, {}
    for lang in ('ko', 'en'):
        raw, expected = sources[lang], source_hashes[lang]
        if not isinstance(raw, bytes) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('source hash mismatch')
        text = raw.decode('utf-8')
        meta, _ = _front_matter_and_body(text)
        if meta.get('lang') != lang or not all(meta.get(k) for k in ('title', 'series', 'date', 'translation_key', 'permalink')):
            raise ValueError('invalid source metadata')
        if not meta['permalink'].startswith('/posts/' if lang == 'ko' else '/en/posts/'):
            raise ValueError('invalid language permalink')
        payload = blogger_request_body(build_blogger_payload(public_copy_from_draft(text), public_base_url='https://oneplate-ai.github.io'))
        original_content = payload['content']
        if not isinstance(original_content, str):
            raise ValueError('invalid content')
        ensure_image_mapping(original_content, mapping)
        content = apply_image_mapping(original_content, mapping)
        if pages_image_sources(content):
            raise ValueError('unmapped image')
        payload['content'] = format_blogger_publication_content(content)
        result[lang], metadata[lang] = payload, meta
    if any(metadata['ko'][key] != metadata['en'][key] for key in ('series', 'date', 'translation_key')) or metadata['ko']['permalink'] == metadata['en']['permalink']:
        raise ValueError('sources do not form a bilingual pair')
    return result
