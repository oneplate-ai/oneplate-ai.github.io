#!/usr/bin/env python3
"""Fail-closed bilingual Pages + Blogger coordinator. Default: read-only plan.

Operational checkouts are never staged or synchronized. Apply uses a retained,
clean isolated clone and a normal non-forced push. No approval is ever created.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import blogger_release as blogger
from publication_gate import GateError, validate_approval


class ReleaseError(RuntimeError):
    """An exact release cannot be completed safely."""


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approval_candidates(root: Path, series: str, day: str) -> list[Path]:
    suffix = r'(?:-oneplate)?' if series == 'weekly-ai' else r'(?:-\d+)?' if series == 'easy-ai' else ''
    pattern = re.compile(re.escape(day + '-' + series) + suffix + r'\.(?:approval\.json|blogger-release\.json|json)$')
    return sorted(p for p in (root / '.publishing-approvals').glob('*.json') if pattern.fullmatch(p.name))


@dataclass
class ReleaseUnit:
    root: Path
    series: str
    day: str
    approval_path: Path
    image_map_path: Path
    approval: dict
    sources: dict
    frozen: dict[Path, bytes]
    candidates: tuple[Path, ...]

    def verify_frozen(self) -> None:
        if tuple(approval_candidates(self.root, self.series, self.day)) != self.candidates:
            raise ReleaseError('approval candidates changed')
        for path, raw in self.frozen.items():
            if not path.is_file() or path.read_bytes() != raw:
                raise ReleaseError(f'frozen artifact changed: {path}')


def load_unit(root: Path, series: str, day: str, approval_path: Path | None = None,
              image_map_path: Path | None = None) -> ReleaseUnit:
    root = root.resolve()
    if series not in {'today-ai-bite', 'weekly-ai', 'easy-ai'} or date.fromisoformat(day).isoformat() != day:
        raise ReleaseError('invalid series/date')
    candidates = approval_candidates(root, series, day)
    paths = sorted(set(candidates + ([approval_path.resolve()] if approval_path else [])))
    if not paths:
        raise ReleaseError('missing approval')
    frozen = {path: path.read_bytes() for path in paths}
    approvals = {path: validate_approval(path, root, series, day) for path in paths}
    semantic = lambda a: {k: sorted(a[k], key=lambda i: i['lang']) if k == 'drafts' else a.get(k)
                         for k in ('drafts', 'series', 'publication_unit', 'scheduled_publish_at',
                                   'blogger_image_map_sha256', 'migration_exception', 'weekly_evidence')}
    if len({fingerprint(semantic(a)) for a in approvals.values()}) != 1:
        raise ReleaseError('conflicting approvals')
    canonical = root / '.publishing-approvals' / f'{day}-{series}.approval.json'
    chosen = approval_path.resolve() if approval_path else min(paths, key=lambda p: (p != canonical, not p.name.endswith('.approval.json'), str(p)))
    approval = approvals[chosen]
    stem = re.sub(r'\.(?:approval|blogger-release)\.json$|\.json$', '', chosen.name)
    canonical_map = root / '.publishing-approvals' / f'{day}-{series}.blogger-image-map.json'
    image_map_path = (image_map_path or (canonical_map if canonical_map.is_file() else chosen.with_name(stem + '.blogger-image-map.json'))).resolve()
    frozen[image_map_path] = image_map_path.read_bytes()
    blogger.verify_image_map_approval(approval, frozen[image_map_path])
    for item in approval['drafts']:
        path = (root / item['exact_draft_path']).resolve()
        if not path.is_relative_to(root):
            raise ReleaseError('draft path escapes editorial root')
        frozen[path] = path.read_bytes()
        if hashlib.sha256(frozen[path]).hexdigest() != item['sha256']:
            raise ReleaseError('draft changed while loading')
    sources = blogger.release_sources(root, chosen, image_map_path)
    unit = ReleaseUnit(root, series, day, chosen, image_map_path, approval, sources, frozen, tuple(candidates))
    unit.verify_frozen()
    return unit


_IMAGE_PATH = re.compile(r'/assets/images/([^\s\"\'<>)}]+)')


def public_files(unit: ReleaseUnit) -> dict[str, bytes]:
    """Only exact approved basenames and image-map-bound, referenced assets."""
    unit.verify_frozen()
    files: dict[str, bytes] = {}
    images: set[str] = set()
    for item in unit.approval['drafts']:
        path = (unit.root / item['exact_draft_path']).resolve()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md', path.name) or not path.name.startswith(unit.day + '-'):
            raise ReleaseError(f'unsafe public post basename: {path.name}')
        public = blogger.public_copy_from_draft(unit.frozen[path].decode('utf-8'))
        files['_posts/' + path.name] = public.encode('utf-8')
        images.update(_IMAGE_PATH.findall(public))
    mapping = json.loads(unit.frozen[unit.image_map_path])
    if set(mapping) != images:
        raise ReleaseError(f'image map contains unknown or missing keys: expected={sorted(images)} actual={sorted(mapping)}')
    for name in sorted(images):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
            raise ReleaseError('unsafe image path')
        sources = [unit.root / 'assets/images' / name, unit.root / 'drafts/images' / name]
        present = [p for p in sources if p.is_file()]
        if not present:
            raise ReleaseError(f'missing approved public asset: {name}')
        if any(not p.resolve().is_relative_to(unit.root) for p in present):
            raise ReleaseError('image path escapes root')
        values = {p.read_bytes() for p in present}
        if len(values) != 1:
            raise ReleaseError(f'conflicting source/public asset: {name}')
        raw = next(iter(values))
        for path in present:
            if path in unit.frozen and unit.frozen[path] != raw:
                raise ReleaseError(f'frozen image changed: {path}')
            unit.frozen[path] = raw
        files['assets/images/' + name] = raw
    unit.verify_frozen()
    return files


def plan_pages(files: dict[str, bytes], remote: dict[str, bytes]) -> list[str]:
    conflicts = sorted(path for path, raw in files.items() if path in remote and remote[path] != raw)
    if conflicts:
        raise ReleaseError('Pages LIVE source conflict: ' + ', '.join(conflicts))
    return sorted(set(files) - set(remote))


def weekly_window(day: str) -> tuple[str, str]:
    release_day = date.fromisoformat(day)
    end = release_day - timedelta(days=(release_day.weekday() - 4) % 7 or 7)
    return (end - timedelta(days=6)).isoformat(), end.isoformat()


class WeeklyEvidenceUnit(Protocol):
    day: str
    approval: dict


def check_weekly_evidence(unit: WeeklyEvidenceUnit, pages: list[dict], posts: list[dict]) -> list[tuple]:
    """Never infer an approved source count from prose or blend different LIVE sets.

    Existing v2 records may bind an optional weekly_evidence object containing
    start/end and posts[{date,lang,title}]. Without it, weekly is blocked, not
    silently certified from today's matching inventories.
    """
    start, end = weekly_window(unit.day)
    def normalized(items):
        result = sorted((i['date'], i['lang'], i['title']) for i in items if start <= i['date'] <= end)
        keys = [(d, lang) for d, lang, _ in result]
        if len(set(keys)) != len(keys) or any(lang not in {'ko', 'en'} for _, lang in keys):
            raise ReleaseError('weekly_evidence_mismatch: duplicate/invalid daily identity')
        if any({lang for d, lang in keys if d == day} != {'ko', 'en'} for day, _ in keys):
            raise ReleaseError('weekly_evidence_mismatch: incomplete bilingual daily pair')
        return result
    actual = normalized(pages)
    if actual != normalized(posts):
        raise ReleaseError('weekly_evidence_mismatch: Pages and Blogger LIVE daily sets differ')
    bound = unit.approval.get('weekly_evidence')
    if not isinstance(bound, dict):
        raise ReleaseError('weekly_evidence_unbound: approved structured source inventory required; prose is not inferred')
    bound_posts = bound.get('posts', [])
    if not isinstance(bound_posts, list) or any(not start <= i['date'] <= end for i in bound_posts):
        raise ReleaseError('weekly_evidence_mismatch: bound inventory extends outside preceding week')
    if bound.get('start') != start or bound.get('end') != end or not actual or normalized(bound_posts) != actual:
        raise ReleaseError('weekly_evidence_mismatch: approved weekly source inventory is stale')
    return actual


def weekly_guard(unit: ReleaseUnit, channels: dict[str, Any]) -> None:
    if unit.series == 'weekly-ai':
        start, end = weekly_window(unit.day)
        if not {'pages', 'blogger'} <= set(channels):
            raise ReleaseError('weekly_evidence_mismatch: both channel inventories required')
        check_weekly_evidence(unit, channels['pages'].daily_inventory(unit, start, end),
                             channels['blogger'].daily_inventory(unit, start, end))


def coordinate(unit: ReleaseUnit, channels: dict[str, Any], *, apply: bool = False) -> dict:
    """Inventory every invocation; isolate failures without rolling back success."""
    if apply:
        blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
    unit.verify_frozen()
    files = public_files(unit)
    weekly_guard(unit, channels)
    results: dict[str, dict] = {}
    plans: dict[str, dict] = {}
    for name, channel in channels.items():
        try:
            plans[name] = channel.plan(unit, files)
            results[name] = {'status': 'planned', 'plan': plans[name]}
        except Exception as error:
            results[name] = {'status': 'failed', 'stage': 'plan', 'error': str(error)}
    if apply:
        for name, channel in channels.items():
            if name not in plans:
                continue
            stage = 'replan'
            completed: dict = {}
            try:
                unit.verify_frozen()
                weekly_guard(unit, channels)
                if channel.plan(unit, files) != plans[name]:
                    raise ReleaseError('channel plan drift; re-inventory and approve a new attempt')
                unit.verify_frozen()
                stage = 'apply'
                completed = channel.apply(unit, files, plans[name])
                stage = 'verify'
                channel.verify(unit, files, completed)
                results[name].update(status='published_verified', completed=completed)
            except Exception as error:
                progress = getattr(channel, 'progress', {})
                if isinstance(progress, dict) and stage in {'apply', 'verify'}:
                    completed = {**progress, **completed}
                results[name].update(status='failed', stage=stage, error=str(error), completed=completed,
                                     executor_stage=completed.get('stage', stage))
    good = sum(r['status'] == ('published_verified' if apply else 'planned') for r in results.values())
    status = ('published_verified' if apply else 'planned') if good == len(channels) else 'partial_failure' if good else 'failed'
    if apply and set(channels) != {'pages', 'blogger'} and status == 'published_verified':
        status = 'partial_failure'
    return {'status': status, 'mode': 'apply' if apply else 'plan',
            'publication_unit': unit.approval['publication_unit'], 'channels': results}


def run_command(args: list[str], cwd: Path | None = None) -> bytes:
    try:
        return subprocess.run(args, cwd=cwd, check=True, capture_output=True, timeout=600).stdout
    except subprocess.CalledProcessError as error:
        # Commands never contain credentials. Do not expose inherited stderr.
        raise ReleaseError(f'command failed ({error.returncode}): {args[0:3]}') from error


class RemoteTree:
    """Read-only GitHub blob inventory pinned to one authoritative main commit."""
    def __init__(self, repo: str, run=run_command):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ReleaseError('invalid remote repository')
        self.repo, self.run = repo, run
        self.head = self.api('git/ref/heads/main')['object']['sha']
        tree = self.api(f'git/trees/{self.head}?recursive=1')
        if tree.get('truncated'):
            raise ReleaseError('remote tree truncated')
        self.entries = {i['path']: i for i in tree['tree']}

    def api(self, endpoint: str):
        return json.loads(self.run(['gh', 'api', f'repos/{self.repo}/{endpoint}']))

    def read(self, paths) -> dict[str, bytes]:
        result = {}
        for path in sorted(paths):
            item = self.entries.get(path)
            if item is None:
                continue
            if item['type'] != 'blob' or item['mode'] not in {'100644', '100755'}:
                raise ReleaseError(f'remote path is not a regular file: {path}')
            blob = self.api('git/blobs/' + item['sha'])
            if blob.get('encoding') != 'base64':
                raise ReleaseError('unsupported remote blob encoding')
            result[path] = base64.b64decode(blob['content'])
        return result


class PagesChannel:
    def __init__(self, repo='oneplate-ai/oneplate-ai.github.io', base_url='https://oneplate-ai.github.io',
                 work_root=Path('/root/.hermes/cache/scratch/dual-channel-release'), *, run=run_command):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ReleaseError('invalid remote repository')
        self.repo, self.base_url, self.work_root, self.run = repo, base_url.rstrip('/'), Path(work_root), run
        self.progress: dict = {}

    def plan(self, unit: ReleaseUnit, files: dict[str, bytes]) -> dict:
        tree = RemoteTree(self.repo, self.run)
        writes = plan_pages(files, tree.read(files))
        return {'head': tree.head, 'writes': writes,
                'public_sha256': {p: hashlib.sha256(v).hexdigest() for p, v in sorted(files.items())},
                'git_writes': ['isolated clone', 'explicit stage', 'commit', 'push HEAD:main'] if writes else []}

    def apply(self, unit: ReleaseUnit, files: dict[str, bytes], plan: dict) -> dict:
        import uuid
        blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
        unit.verify_frozen()
        self.progress = {'commit': plan['head'], 'pushed': False}
        if not plan['writes']:
            return self.progress.copy()
        folder = self.work_root.resolve() / ('release-' + uuid.uuid4().hex)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.progress.update(worktree=str(folder), stage='clone')
        self.run(['git', 'clone', '--no-checkout', '--branch', 'main', '--', f'https://github.com/{self.repo}.git', str(folder)])
        command = lambda args: self.run(args, cwd=folder)
        if command(['git', 'rev-parse', 'origin/main']).decode().strip() != plan['head']:
            raise ReleaseError('remote main drift before checkout; isolated clone retained')
        command(['git', 'checkout', '--detach', plan['head']])
        if command(['git', 'status', '--porcelain=v1', '-z']):
            raise ReleaseError('isolated checkout is not clean')
        for path in plan['writes']:
            unit.verify_frozen()
            if path not in files:
                raise ReleaseError('write scope escaped approved files')
            target = folder / path
            if not target.resolve().is_relative_to(folder) or target.is_symlink():
                raise ReleaseError('write path escapes isolated checkout')
            if target.exists() and target.read_bytes() != files[path]:
                raise ReleaseError(f'isolated LIVE conflict: {path}')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[path])
        self.progress['stage'] = 'build'
        docker = ['docker', 'compose', '--project-name', folder.name, 'run', '--rm', 'jekyll']
        for check in (['bundle', 'exec', 'jekyll', 'build', '--trace'],
                      ['ruby', 'scripts/validate_bilingual_publication.rb'],
                      ['ruby', 'scripts/validate_editorial_rules.rb', '--strict']):
            command(docker + check)
        for path, raw in files.items():
            target = folder / path
            if not target.is_file() or target.is_symlink() or target.read_bytes() != raw:
                raise ReleaseError(f'materialized approved bytes changed during build: {path}')
        def names(args):
            return {p for p in command(args).decode().split('\0') if p}
        changed = names(['git', 'diff', '--name-only', '-z']) | names(['git', 'ls-files', '--others', '--exclude-standard', '-z'])
        if changed != set(plan['writes']):
            raise ReleaseError(f'build/write scope mismatch: {sorted(changed)}')
        command(['git', 'diff', '--check'])
        unit.verify_frozen()
        command(['git', 'add', '--'] + plan['writes'])
        if names(['git', 'diff', '--name-only', '--cached', '-z']) != set(plan['writes']):
            raise ReleaseError('staged scope mismatch')
        self.progress['stage'] = 'commit'
        command(['git', 'commit', '-m', f"Publish {unit.approval['publication_unit']}"])
        self.progress['commit'] = command(['git', 'rev-parse', 'HEAD']).decode().strip()
        unit.verify_frozen()
        self.progress['stage'] = 'push'
        command(['git', 'push', 'origin', 'HEAD:main'])
        self.progress.update(pushed=True, stage='pushed')
        return self.progress.copy()

    def daily_inventory(self, unit: ReleaseUnit | None, start: str, end: str) -> list[dict]:
        from blogger_adapter import _front_matter_and_body
        from html import unescape
        tree = RemoteTree(self.repo, self.run)
        paths = sorted(p for p in tree.entries if re.fullmatch(r'_posts/\d{4}-\d{2}-\d{2}-.+\.md', p)
                       and start <= Path(p).name[:10] <= end)
        result = []
        for raw in tree.read(paths).values():
            metadata, _ = _front_matter_and_body(raw.decode('utf-8'))
            if metadata.get('series') != 'today-ai-bite' or metadata.get('published', 'true') == 'false':
                continue
            if metadata.get('draft') == 'true' or metadata.get('noindex') == 'true':
                continue
            day = date.fromisoformat(metadata['date'][:10]).isoformat()
            if start <= day <= end:
                status, headers, html = fetch_url(urljoin(self.base_url + '/', metadata['permalink']))
                if status != 200 or metadata['title'] not in unescape(html.decode('utf-8')) or 'noindex' in headers.get('x-robots-tag', '').lower():
                    raise ReleaseError('weekly_evidence_mismatch: Pages daily source is not publicly LIVE')
                result.append({'date': day, 'lang': metadata['lang'], 'title': metadata['title']})
        return sorted(result, key=lambda i: (i['date'], i['lang'], i['title']))

    def wait_deploy(self, commit: str, timeout: float = 300) -> None:
        deadline = time.monotonic() + timeout
        while True:
            runs = json.loads(self.run(['gh', 'run', 'list', '--repo', self.repo, '--workflow',
                'pages-deploy.yml', '--commit', commit, '--limit', '20', '--json', 'status,conclusion']))
            if runs and runs[0]['status'] == 'completed':
                if runs[0]['conclusion'] != 'success':
                    raise ReleaseError(f"Pages build failed: {runs[0]['conclusion']}")
                return
            if time.monotonic() >= deadline:
                raise ReleaseError(f'Pages deployment timeout for commit {commit}')
            time.sleep(min(5, max(0, deadline - time.monotonic())))

    def verify(self, unit: ReleaseUnit, files: dict[str, bytes], completed: dict) -> None:
        tree = RemoteTree(self.repo, self.run)
        if tree.read(files) != files:
            raise ReleaseError('Pages remote source read-back differs from approved public pair/assets')
        self.wait_deploy(completed['commit'])
        verify_pages_web(unit, self.base_url)


def fetch_url(url: str) -> tuple[int, dict[str, str], bytes]:
    if urlparse(url).scheme != 'https':
        raise ReleaseError('anonymous read-back requires HTTPS')
    request = Request(url, headers={'User-Agent': 'AI-One-Plate dual-channel verifier'})
    with urlopen(request, timeout=30) as response:
        return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()


def verify_pages_web(unit: ReleaseUnit, base_url: str, *, fetch=fetch_url) -> None:
    from html.parser import HTMLParser
    import xml.etree.ElementTree as ET
    class Page(HTMLParser):
        def __init__(self, raw):
            super().__init__(convert_charrefs=True)
            self.tags, self.text = [], []
            self.feed(raw.decode('utf-8'))
        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
        def handle_data(self, data):
            self.text.append(data)
    def get(url):
        status, headers, raw = fetch(url)
        if status != 200:
            raise ReleaseError(f'HTTP {status}: {url}')
        if 'noindex' in headers.get('x-robots-tag', '').lower():
            raise ReleaseError(f'noindex response header: {url}')
        return headers, raw
    def page(url):
        return Page(get(url)[1])
    def links(p, url):
        return {urljoin(url, a['href']) for tag, a in p.tags if tag == 'a' and 'href' in a}
    base = base_url.rstrip('/') + '/'
    urls = {d['lang']: urljoin(base, d['permalink']) for d in unit.approval['drafts']}
    css = set()
    for item in unit.approval['drafts']:
        url = urls[item['lang']]
        p = page(url)
        if item['title'] not in ''.join(p.text):
            raise ReleaseError(f'Pages title missing: {url}')
        if any(tag == 'meta' and a.get('name', '').lower() == 'robots' for tag, a in p.tags):
            raise ReleaseError(f'robots meta must be absent: {url}')
        canonical = [a.get('href') for tag, a in p.tags if tag == 'link' and a.get('rel') == 'canonical']
        if canonical != [url]:
            raise ReleaseError(f'canonical mismatch: {url}')
        alternates = {(a.get('hreflang'), a.get('href')) for tag, a in p.tags if tag == 'link' and a.get('rel') == 'alternate'}
        if not {('ko-KR', urls['ko']), ('en', urls['en'])} <= alternates:
            raise ReleaseError(f'reciprocal hreflang missing: {url}')
        css.update(urljoin(url, a['href']) for tag, a in p.tags if tag == 'link' and a.get('rel') == 'stylesheet' and a.get('href'))
        prefix = 'en/' if item['lang'] == 'en' else ''
        home = urljoin(base, prefix)
        series_url = urljoin(base, prefix + f'series/{unit.series}/')
        home_links = links(page(home), home)
        if url not in home_links and series_url not in home_links:
            raise ReleaseError(f'homepage post/series link missing: {url}')
        series_page = page(series_url)
        if url not in links(series_page, series_url) and item['title'] not in ''.join(series_page.text):
            raise ReleaseError(f'series title/link missing: {url}')
    sitemap = ET.fromstring(get(urljoin(base, 'sitemap.xml'))[1])
    locs = [e.text for e in sitemap.iter() if e.tag.rsplit('}', 1)[-1] == 'loc']
    if any(locs.count(url) != 1 for url in urls.values()):
        raise ReleaseError('sitemap must contain each approved URL exactly once')
    if not css:
        raise ReleaseError('stylesheet missing')
    for url in sorted(css):
        headers, _ = get(url)
        if headers.get('content-type', '').split(';')[0].strip().lower() != 'text/css':
            raise ReleaseError(f'CSS content-type mismatch: {url}')
    for name in json.loads(unit.frozen[unit.image_map_path]):
        get(urljoin(base, 'assets/images/' + name))


class GuardedBloggerClient(blogger.BloggerClient):
    """Constrain every mutation to approved keys/known IDs; rehash before send."""
    def __init__(self, token, blog_id, unit, progress):
        super().__init__(token, blog_id)
        self.unit, self.progress = unit, progress
        self.allowed_ids: set[str] | None = None

    def authorize(self, plan: dict) -> None:
        self.allowed_ids = {i['id'] for i in plan['inventory']}
        self.creates = {tuple(c['key']) for c in plan['writes'] if c['action'] == 'create_draft_then_publish'}

    def _request(self, method, path, *, query=None, body=None):
        if method == 'GET':
            return super()._request(method, path, query=query, body=body)
        self.unit.verify_frozen()
        prefix = f'/blogs/{self.blog_id}/posts'
        if self.allowed_ids is None:
            raise ReleaseError('Blogger mutation not authorized by apply plan')
        if path == prefix:
            if method != 'POST' or query != {'isDraft': 'true'}:
                raise ReleaseError('Blogger mutation scope escaped draft creation')
        else:
            match = re.fullmatch(re.escape(prefix) + r'/([^/]+)(/publish)?', path)
            if not match or match[1] not in self.allowed_ids or method not in {'PUT', 'POST'}:
                raise ReleaseError('Blogger mutation scope escaped approved IDs')
            if bool(match[2]) != (method == 'POST'):
                raise ReleaseError('Blogger mutation scope escaped update/publish')
        if (path == prefix or method == 'PUT') and not isinstance(body, dict):
            raise ReleaseError('Blogger mutation requires approved payload')
        key = None
        if body is not None:
            labels = body.get('labels', [])
            if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
                raise ReleaseError('Blogger payload labels are invalid')
            identity = blogger.label_identity(labels)
            key = (str(body.get('title', '')), identity[1] if identity else '')
            source = self.unit.sources.get(key)
            if path == prefix and key not in self.creates:
                raise ReleaseError('unplanned Blogger draft creation')
            content = blogger.strip_language_switcher(blogger.strip_navigation(str(body.get('content', ''))))
            if source is None or sorted(labels) != source['labels'] or content.strip() != source['content'].strip():
                raise ReleaseError('Blogger payload scope differs from approved prose/labels')
        self.progress['stage'] = 'api_mutation'
        result = super()._request(method, path, query=query, body=body)
        post_id = str(result.get('id', ''))
        self.progress.setdefault('mutations', []).append({'method': method, 'path': path, 'id': post_id})
        if path == prefix and post_id and key is not None:
            self.allowed_ids.add(post_id)
            self.creates.discard(key)
        return result


class BloggerChannel:
    def __init__(self, *, blog_id=blogger.DEFAULT_BLOG_ID, token_path=blogger.DEFAULT_TOKEN_PATH,
                 client_secret_path=blogger.DEFAULT_CLIENT_SECRET_PATH, client_factory=None):
        self.blog_id, self.token_path, self.client_secret_path = blog_id, token_path, client_secret_path
        self.client_factory = client_factory or self.new_client
        self.progress: dict = {}

    def daily_inventory(self, unit: ReleaseUnit | None, start: str, end: str) -> list[dict]:
        result = []
        for post in self.client_factory(unit).list_posts('LIVE'):
            identity = blogger.label_identity(post.get('labels', []))
            if identity is None or identity[0] != 'today-ai-bite':
                continue
            timestamp = datetime.fromisoformat(post['published'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ReleaseError('weekly_evidence_mismatch: Blogger LIVE timestamp lacks timezone')
            day = timestamp.astimezone(timezone(timedelta(hours=9))).date().isoformat()
            if start <= day <= end:
                result.append({'date': day, 'lang': identity[1], 'title': str(post['title'])})
        return sorted(result, key=lambda i: (i['date'], i['lang'], i['title']))

    def new_client(self, unit: ReleaseUnit | None):
        token = blogger.fresh_access_token(self.token_path, self.client_secret_path)
        return blogger.ReleaseClient(GuardedBloggerClient(token, self.blog_id, unit, self.progress))

    def plan(self, unit: ReleaseUnit, files: dict[str, bytes]) -> dict:
        self.release = self.client_factory(unit)
        current = self.release.current(set(unit.sources))
        for key, (state, post) in current.items():
            if state == 'LIVE':
                body = blogger.strip_language_switcher(blogger.strip_navigation(str(post.get('content', ''))))
                if body.strip() != unit.sources[key]['content'].strip() or sorted(post.get('labels', [])) != unit.sources[key]['labels']:
                    raise ReleaseError('Blogger LIVE body/labels conflict; publication cannot rewrite existing prose')
        changes = blogger.plan_release(unit.sources, current)
        if any(c['action'] == 'update_live' for c in changes):
            raise ReleaseError('Blogger LIVE body/labels conflict; publication cannot rewrite existing prose')
        inventory = [{'key': key, 'state': state, 'id': str(post['id']),
                      'sha256': fingerprint(post)} for key, (state, post) in sorted(current.items())]
        return {'writes': changes, 'inventory': inventory}

    def apply(self, unit: ReleaseUnit, files: dict[str, bytes], plan: dict) -> dict:
        blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
        unit.verify_frozen()
        self.progress.clear()
        self.progress['known_ids'] = [i['id'] for i in plan['inventory']]
        if isinstance(self.release.client, GuardedBloggerClient):
            self.release.client.authorize(plan)
        result = self.release.apply(unit.sources, plan['writes'])
        self.progress.update(result)
        return result

    def verify(self, unit: ReleaseUnit, files: dict[str, bytes], completed: dict) -> None:
        # A new client, new token, and independent API inventory, not mutation responses.
        release = self.client_factory(unit)
        current = release.verify(unit.sources)
        blogger.verify_blogger_public_pair(unit.sources, current)
        if blogger.plan_release(unit.sources, current):
            raise ReleaseError('Blogger final read-only plan is not zero-change')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='canonical editorial root (untracked drafts allowed)')
    parser.add_argument('--series', choices=('today-ai-bite', 'weekly-ai', 'easy-ai'), required=True)
    parser.add_argument('--date', required=True, dest='day')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--image-urls', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--remote-repo', '--repo', default='oneplate-ai/oneplate-ai.github.io')
    parser.add_argument('--pages-base-url', default='https://oneplate-ai.github.io')
    parser.add_argument('--work-root', type=Path, default=Path('/root/.hermes/cache/scratch/dual-channel-release'))
    parser.add_argument('--blog-id', default=blogger.DEFAULT_BLOG_ID)
    parser.add_argument('--token-path', type=Path, default=blogger.DEFAULT_TOKEN_PATH)
    parser.add_argument('--client-secret-path', type=Path, default=blogger.DEFAULT_CLIENT_SECRET_PATH)
    args = parser.parse_args(argv)
    try:
        unit = load_unit(args.root, args.series, args.day, args.approval, args.image_urls)
        if args.apply:
            # Before clone/mkdir/build/API writes, including executor construction.
            blogger.require_immediate_publish_time(unit.approval['scheduled_publish_at'])
        channels = {'pages': PagesChannel(args.remote_repo, args.pages_base_url, args.work_root),
                    'blogger': BloggerChannel(blog_id=args.blog_id, token_path=args.token_path,
                                              client_secret_path=args.client_secret_path)}
        result = coordinate(unit, channels, apply=args.apply)
    except Exception as error:
        result = {'status': 'blocked', 'mode': 'apply' if args.apply else 'plan', 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in {'planned', 'published_verified'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
