#!/usr/bin/env python3
"""Create a schema-versioned bilingual approval record from exact drafts."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from publication_gate import ROOT, front_matter, sha256, validate_approval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ko", type=Path, required=True, help="한국어 drafts/ 파일")
    parser.add_argument("--en", type=Path, required=True, help="영어 drafts/ 파일")
    parser.add_argument("--approval-text", required=True)
    parser.add_argument("--scheduled-publish-at", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    paths = [("ko", args.ko.resolve()), ("en", args.en.resolve())]
    try:
        metadata = [front_matter(path) for _, path in paths]
        series = {item.get("series") for item in metadata}
        dates = {item.get("date") for item in metadata}
        keys = {item.get("translation_key") for item in metadata}
        if len(series) != 1 or len(dates) != 1 or len(keys) != 1:
            raise ValueError("한·영 초안의 series/date/translation_key가 일치하지 않습니다.")
        series_value = next(iter(series))
        date_value = next(iter(dates))
        if not series_value or not date_value:
            raise ValueError("초안 front matter에 series와 date가 필요합니다.")
        drafts = []
        for lang, path in paths:
            item = front_matter(path)
            expected_prefix = "/en/posts/" if lang == "en" else "/posts/"
            permalink = item.get("permalink", "")
            if item.get("lang") != lang or not permalink.startswith(expected_prefix):
                raise ValueError(f"{lang} 초안의 lang/permalink가 올바르지 않습니다: {path}")
            drafts.append({
                "lang": lang,
                "exact_draft_path": str(path.relative_to(root)),
                "sha256": sha256(path),
                "title": item.get("title", ""),
                "date": date_value,
                "translation_key": next(iter(keys)),
                "permalink": permalink,
            })
        record = {
            "schema_version": 2,
            "publication_unit": f"{series_value}-{date_value}-bilingual",
            "series": series_value,
            "approved_at": args.approved_at,
            "scheduled_publish_at": args.scheduled_publish_at,
            "approval_text_literal": args.approval_text,
            "drafts": drafts,
        }
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validate_approval(output, root, series_value, date_value)
        print(f"승인 기록 생성·검증 성공: {output}")
        return 0
    except (OSError, ValueError) as error:
        print(f"승인 기록 생성 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
