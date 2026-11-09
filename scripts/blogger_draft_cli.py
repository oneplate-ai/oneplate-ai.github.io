#!/usr/bin/env python3
"""Plan or explicitly apply exact-pair Blogger drafts; never publish."""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import blogger_draft_create as create
import blogger_draft_bound_update as update
from blogger_draft_guarded_transport import GuardedTransport
from blogger_full_parity_sync import fresh_access_token
from blogger_publish import BloggerClient
from blogger_draft_generic import prepare_pair
from blogger_draft_plan import digest, plan_drafts


def prepare_create(blog_id, paths, image_path, output):
    def read(path):
        path = os.path.abspath(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('not a regular file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read()
        finally:
            os.close(fd)
        return raw, {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
    source = {lang: read(paths[lang]) for lang in ('ko', 'en')}
    image, image_entry = read(image_path)
    payloads = prepare_pair({k: v[0] for k, v in source.items()},
                            {k: v[1]['sha256'] for k, v in source.items()}, image, image_entry['sha256'])
    plan_drafts([payloads['ko'], payloads['en']], [], {})
    manifest = {'blog_id': blog_id, 'sources': {k: v[1] for k, v in source.items()},
                'image_map': image_entry, 'payload_sha256': {k: digest(v) for k, v in payloads.items()}}
    data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {'manifest_sha256': hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare-create', help='원본과 이미지 맵으로 오프라인 manifest 생성')
    prep.add_argument('--blog-id', required=True)
    prep.add_argument('--ko-source', required=True, type=Path)
    prep.add_argument('--en-source', required=True, type=Path)
    prep.add_argument('--image-map', required=True, type=Path)
    prep.add_argument('--output', required=True, type=Path)
    for name in ('create', 'update'):
        p = sub.add_parser(name)
        p.add_argument('--blog-id', required=True)
        p.add_argument('--token-path', required=True, type=Path)
        p.add_argument('--client-secret-path', required=True, type=Path)
        p.add_argument('--manifest', type=Path)
        p.add_argument('--manifest-sha256')
        p.add_argument('--apply', action='store_true')
        if name == 'update':
            p.add_argument('--ko-source', type=Path)
            p.add_argument('--en-source', type=Path)
            p.add_argument('--image-map', type=Path)
            p.add_argument('--ko-id')
            p.add_argument('--en-id')
            p.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[0-9]+', args.blog_id):
            raise ValueError()
        if args.operation == 'prepare-create':
            result = prepare_create(args.blog_id, {'ko': args.ko_source, 'en': args.en_source}, args.image_map, args.output)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.operation == 'create':
            if not args.manifest or not re.fullmatch(r'[0-9a-f]{64}', args.manifest_sha256 or ''):
                raise ValueError()
        elif args.apply:
            if not args.manifest or not re.fullmatch(r'[0-9a-f]{64}', args.manifest_sha256 or ''):
                raise ValueError()
            manifest = json.loads(args.manifest.read_bytes())
            if update.manifest_hash(manifest) != args.manifest_sha256 or manifest['update']['blog_id'] != args.blog_id:
                raise ValueError()
            paths = {lang: manifest['sources'][lang]['path'] for lang in ('ko', 'en')}
            image = manifest['image_map']['path']
        elif not all((args.ko_source, args.en_source, args.image_map, args.ko_id, args.en_id, args.output)):
            raise ValueError()
        token = fresh_access_token(args.token_path, args.client_secret_path)
        request = GuardedTransport(BloggerClient(token, args.blog_id))
        if args.operation == 'create':
            if args.apply:
                result = create.run(request, args.blog_id, args.manifest, args.manifest_sha256, None)
            else:
                result = create.plan(request, args.blog_id, args.manifest, args.manifest_sha256)
        elif args.apply:
            result = update.apply(request, manifest, args.manifest_sha256, paths, image, None)
        else:
            manifest = update.plan(request, args.blog_id, {'ko': args.ko_id, 'en': args.en_id},
                                   {'ko': args.ko_source, 'en': args.en_source}, args.image_map)
            data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            result = {'manifest_path': str(args.output), 'manifest_sha256': update.manifest_hash(manifest)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print('작업이 중단되었습니다. 입력과 승인 해시를 확인하고, 적용을 시도했다면 재시도 전에 저널과 원격 상태를 수동 대조하세요.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
