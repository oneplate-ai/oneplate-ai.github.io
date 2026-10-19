#!/usr/bin/env python3
"""Export a read-only, shared LIVE weekly inventory; never approves or publishes."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from dual_channel_release import PagesChannel, BloggerChannel, ReleaseError, check_weekly_evidence, weekly_window


def validate_evidence(day, evidence):
    if date.fromisoformat(day).weekday() != 5:
        raise ValueError('weekly publication date must be Saturday')
    if not isinstance(evidence, dict) or set(evidence) != {'start', 'end', 'posts'}:
        raise ValueError('weekly evidence must contain start/end/posts')
    posts = evidence['posts']
    if not isinstance(posts, list) or not posts:
        raise ValueError('weekly evidence is empty')
    for item in posts:
        if (not isinstance(item, dict) or set(item) != {'date', 'lang', 'title'}
                or not all(isinstance(v, str) and v.strip() for v in item.values())
                or item['lang'] not in {'ko', 'en'}):
            raise ValueError('invalid weekly source identity')
        if date.fromisoformat(item['date']).isoformat() != item['date']:
            raise ValueError('invalid weekly source date')
    unit = SimpleNamespace(day=day, approval={'weekly_evidence': evidence})
    try:
        rows = check_weekly_evidence(unit, posts, posts)
    except (ReleaseError, KeyError, TypeError) as error:
        raise ValueError(str(error)) from error
    return {'start': evidence['start'], 'end': evidence['end'],
            'posts': [dict(date=d, lang=lang, title=title) for d, lang, title in rows]}


def build_evidence(day, pages, blogger):
    start, end = weekly_window(day)
    evidence = validate_evidence(day, {'start': start, 'end': end, 'posts': pages})
    unit = SimpleNamespace(day=day, approval={'weekly_evidence': evidence})
    try:
        check_weekly_evidence(unit, pages, blogger)
    except (ReleaseError, KeyError, TypeError) as error:
        raise ValueError(str(error)) from error
    return evidence


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date', required=True, dest='day')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        # These inventory methods perform reads only and do not need a release approval.
        start, end = weekly_window(args.day)
        evidence = build_evidence(args.day,
            PagesChannel().daily_inventory(None, start, end),
            BloggerChannel().daily_inventory(None, start, end))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite evidence that may already be associated with a Preview/approval.
        with args.output.open('x', encoding='utf-8') as out:
            out.write(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'weekly_evidence_ready', 'output': str(args.output),
                          'source_pairs': len(evidence['posts']) // 2}))
        return 0
    except (OSError, ValueError, ReleaseError) as error:
        print(json.dumps({'status':'weekly_evidence_blocked','error':str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
