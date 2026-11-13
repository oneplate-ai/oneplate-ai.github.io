#!/usr/bin/env python3
"""Export Blogger LIVE weekly evidence; never approves or publishes.

Legacy dual-channel evidence remains readable, but newly exported evidence is
explicitly Blogger-only. A source inventory is never duplicated to fake parity.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dual_channel_release import ReleaseError, weekly_window


def validate_evidence(day, evidence):
    if date.fromisoformat(day).weekday() != 5:
        raise ValueError('weekly publication date must be Saturday')
    if not isinstance(evidence, dict):
        raise ValueError('invalid weekly evidence')
    fields = set(evidence)
    legacy = fields == {'start', 'end', 'posts'}
    if not legacy and (fields != {'schema_version', 'source_channel', 'start', 'end', 'posts'}
                       or type(evidence['schema_version']) is not int
                       or evidence['schema_version'] != 1 or evidence['source_channel'] != 'blogger'):
        raise ValueError('invalid weekly evidence schema/channel')
    start, end = weekly_window(day)
    if evidence['start'] != start or evidence['end'] != end:
        raise ValueError('weekly evidence window mismatch')
    posts = evidence['posts']
    if not isinstance(posts, list) or not posts:
        raise ValueError('weekly evidence is empty')
    rows = []
    for item in posts:
        if (not isinstance(item, dict) or set(item) != {'date', 'lang', 'title'}
                or not all(isinstance(v, str) and v.strip() for v in item.values())
                or item['lang'] not in {'ko', 'en'}):
            raise ValueError('invalid weekly source identity')
        if date.fromisoformat(item['date']).isoformat() != item['date'] or not start <= item['date'] <= end:
            raise ValueError('invalid weekly source date')
        rows.append((item['date'], item['lang'], item['title']))
    keys = [(d, lang) for d, lang, _ in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate weekly source identity')
    if any({lang for d, lang in keys if d == day} != {'ko', 'en'} for day, _ in keys):
        raise ValueError('incomplete bilingual weekly source pair')
    result = {'start': start, 'end': end,
              'posts': [dict(date=d, lang=lang, title=title) for d, lang, title in sorted(rows)]}
    if not legacy:
        result = {'schema_version': 1, 'source_channel': 'blogger', **result}
    return result


def build_blogger_evidence(day, posts):
    start, end = weekly_window(day)
    return validate_evidence(day, {'schema_version': 1, 'source_channel': 'blogger',
                                   'start': start, 'end': end, 'posts': posts})


def build_evidence(day, pages, blogger):
    """Historical dual-channel builder: compare independent inventories."""
    start, end = weekly_window(day)
    evidence = validate_evidence(day, {'start': start, 'end': end, 'posts': pages})
    other = validate_evidence(day, {'start': start, 'end': end, 'posts': blogger})
    if evidence != other:
        raise ValueError('weekly_evidence_mismatch: Pages and Blogger LIVE daily sets differ')
    return evidence


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date', required=True, dest='day')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        # Delayed import avoids a module cycle with the publication guard.
        start, end = weekly_window(args.day)
        evidence = build_blogger_evidence(args.day, BloggerChannel().daily_inventory(None, start, end))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as out:
            out.write(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'weekly_evidence_ready', 'output': str(args.output),
                          'source_channel': 'blogger', 'source_pairs': len(evidence['posts']) // 2}))
        return 0
    except (OSError, ValueError, ReleaseError) as error:
        print(json.dumps({'status': 'weekly_evidence_blocked', 'error': str(error)}))
        return 1


# Imported after pure validators so the coordinator can reuse them.
from blogger_channel_release import BloggerChannel

if __name__ == '__main__':
    sys.exit(main())
