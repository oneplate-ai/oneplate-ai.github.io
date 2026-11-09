#!/usr/bin/env python3
"""Offline, read-only Blogger draft plan. This module cannot write to Blogger.

This is only a planning component, not an apply tool. Inventory must contain
all pages of LIVE, DRAFT and SCHEDULED posts with explicit status fields.
"""
from __future__ import annotations

import hashlib
import json
import re


class DraftPlanError(ValueError):
    pass


def identity(labels):
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise DraftPlanError('invalid labels')
    found = [re.fullmatch(r'(weekly-ai|today-ai-bite|easy-ai)-(ko|en)', x) for x in labels]
    found = [x for x in found if x]
    if len(found) != 1:
        raise DraftPlanError('ambiguous language or series labels')
    return found[0].groups()


def digest(post):
    return hashlib.sha256(json.dumps(post, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def plan_drafts(payloads, inventory, explicit_ids):
    if len(payloads) != 2 or not isinstance(explicit_ids, dict):
        raise DraftPlanError('exactly two payloads and an explicit ID mapping required')
    identities = [identity(p.get('labels')) for p in payloads]
    if {x[1] for x in identities} != {'ko', 'en'} or len({x[0] for x in identities}) != 1:
        raise DraftPlanError('expected one same-series bilingual pair')
    if any(not isinstance(p.get('title'), str) or not p['title'] or not isinstance(p.get('content'), str) for p in payloads):
        raise DraftPlanError('invalid payload')
    if len({p['title'] for p in payloads}) != 2:
        raise DraftPlanError('duplicate titles')
    if not set(explicit_ids) <= {'ko', 'en'} or any(not isinstance(v, str) or not re.fullmatch(r'[0-9]+', v) for v in explicit_ids.values()) or len(set(explicit_ids.values())) != len(explicit_ids):
        raise DraftPlanError('invalid explicit IDs')
    ids = set()
    for post in inventory:
        if not isinstance(post, dict) or post.get('status') not in {'LIVE', 'DRAFT', 'SCHEDULED'} or not isinstance(post.get('id'), str) or not re.fullmatch(r'[0-9]+', post['id']) or post['id'] in ids:
            raise DraftPlanError('invalid or duplicated inventory record')
        ids.add(post['id'])
    plan = []
    for payload, expected in zip(payloads, identities):
        language = expected[1]
        matches = [post for post in inventory if post.get('title') == payload['title']]
        desired_id = explicit_ids.get(language)
        if desired_id is None:
            if matches:
                raise DraftPlanError('existing title requires explicit ID and must be a matching draft')
            plan.append({'language': language, 'action': 'create', 'payload_sha256': digest(payload)})
            continue
        selected = [post for post in inventory if post['id'] == desired_id]
        if len(selected) != 1 or len(matches) != 1 or selected[0] is not matches[0]:
            raise DraftPlanError('explicit ID or title collision')
        post = selected[0]
        if post['status'] != 'DRAFT' or identity(post.get('labels')) != expected:
            raise DraftPlanError('target is not the matching draft')
        equal = all(post.get(k) == payload.get(k) for k in ('title', 'content', 'labels'))
        plan.append({'language': language, 'action': 'none' if equal else 'update', 'id': desired_id,
                     'before_sha256': digest(post), 'payload_sha256': digest(payload)})
    return plan
