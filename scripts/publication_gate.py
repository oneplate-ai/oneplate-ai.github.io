#!/usr/bin/env python3
"""Fail-closed checks for AI One Plate publication units.

This gate validates durable approval records, publication scope, and weekly
source inventory without changing repository or remote state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DRAFT_KEYS = {
    "lang",
    "exact_draft_path",
    "sha256",
    "title",
    "date",
    "translation_key",
    "permalink",
}
REQUIRED_APPROVAL_KEYS = {
    "schema_version",
    "publication_unit",
    "series",
    "approved_at",
    "scheduled_publish_at",
    "approval_text_literal",
    "drafts",
}
POST_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>.+)\.md$")


class GateError(ValueError):
    """An approval or publication gate failed."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise GateError(f"front matter가 없습니다: {path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise GateError(f"front matter 종료가 없습니다: {path}")
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def relative_inside(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise GateError(f"허용되지 않은 초안 경로: {value}")
    resolved = (root / path).resolve()
    drafts = (root / "drafts").resolve()
    if resolved != drafts and drafts not in resolved.parents:
        raise GateError(f"초안은 drafts/ 아래에 있어야 합니다: {value}")
    return resolved


def validate_approval(path: Path, root: Path = ROOT, expected_series: str | None = None,
                      expected_date: str | None = None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError(f"승인 기록 JSON을 읽을 수 없습니다: {path}: {error}") from error

    missing = REQUIRED_APPROVAL_KEYS - data.keys()
    if missing:
        raise GateError(f"승인 기록 필수 키 누락: {', '.join(sorted(missing))}")
    if data["schema_version"] != 2:
        raise GateError("승인 기록은 schema_version 2여야 합니다.")
    if not isinstance(data["approval_text_literal"], str) or not data["approval_text_literal"].strip():
        raise GateError("approval_text_literal이 비어 있습니다.")
    series = data["series"]
    if not isinstance(series, str) or not re.fullmatch(r"[a-z0-9-]+", series):
        raise GateError(f"series가 안전한 slug가 아닙니다: {series!r}")
    if expected_series and series != expected_series:
        raise GateError(f"승인 시리즈 불일치: expected={expected_series}, actual={series}")
    drafts = data["drafts"]
    if not isinstance(drafts, list) or len(drafts) != 2:
        raise GateError("승인 기록에는 한국어·영어 초안 2개가 필요합니다.")

    languages = []
    translation_keys = set()
    seen_paths = set()
    for item in drafts:
        if not isinstance(item, dict):
            raise GateError("drafts 항목은 객체여야 합니다.")
        missing = REQUIRED_DRAFT_KEYS - item.keys()
        if missing:
            raise GateError(f"초안 항목 필수 키 누락: {', '.join(sorted(missing))}")
        lang = item["lang"]
        if lang not in {"ko", "en"} or lang in languages:
            raise GateError(f"lang은 ko·en 각각 한 번씩만 있어야 합니다: {lang!r}")
        languages.append(lang)
        draft_path = relative_inside(root, item["exact_draft_path"])
        if not draft_path.is_file():
            raise GateError(f"초안 파일이 없습니다: {item['exact_draft_path']}")
        actual_hash = sha256(draft_path)
        if actual_hash != item["sha256"]:
            raise GateError(
                f"초안 SHA-256 불일치: {item['exact_draft_path']} "
                f"expected={item['sha256']} actual={actual_hash}"
            )
        metadata = front_matter(draft_path)
        for key in ("lang", "date", "translation_key", "permalink", "series"):
            if key not in metadata:
                raise GateError(f"초안 front matter에 {key}가 없습니다: {draft_path}")
        if metadata["lang"] != lang:
            raise GateError(f"초안 언어 불일치: {draft_path}")
        if metadata["series"] != series:
            raise GateError(f"초안 series 불일치: {draft_path}")
        if metadata["date"] != item["date"]:
            raise GateError(f"승인 기록과 초안 날짜 불일치: {draft_path}")
        if expected_date and item["date"] != expected_date:
            raise GateError(f"승인 날짜 불일치: expected={expected_date}, actual={item['date']}")
        if metadata["translation_key"] != item["translation_key"]:
            raise GateError(f"translation_key 불일치: {draft_path}")
        expected_prefix = "/en/posts/" if lang == "en" else "/posts/"
        if not item["permalink"].startswith(expected_prefix) or metadata["permalink"] != item["permalink"]:
            raise GateError(f"permalink 불일치 또는 언어 경로 오류: {draft_path}")
        translation_keys.add(item["translation_key"])
        seen_paths.add(str(draft_path))

    if set(languages) != {"ko", "en"}:
        raise GateError("승인 기록은 ko·en 쌍이어야 합니다.")
    if len(translation_keys) != 1:
        raise GateError("한·영 초안의 translation_key가 서로 다릅니다.")
    expected_unit = f"{series}-{data['drafts'][0]['date']}-bilingual"
    if data["publication_unit"] != expected_unit:
        raise GateError(f"publication_unit 불일치: expected={expected_unit}, actual={data['publication_unit']}")
    return data


def allowed_public_paths(series: str, publish_date: str) -> set[str]:
    suffix = "weekly-ai-oneplate" if series == "weekly-ai" else series
    return {
        f"_posts/{publish_date}-{suffix}.md",
        f"_posts/{publish_date}-{suffix}-en.md",
        "index.md",
        "en/index.md",
        f"series/{series}/index.md",
        f"en/series/{series}/index.md",
        "sitemap.xml",
    }


def validate_scope(paths: Iterable[str], series: str, publish_date: str) -> list[str]:
    normalized = [p.replace("\\", "/") for p in paths if p.strip()]
    allowed = allowed_public_paths(series, publish_date)
    unexpected = sorted(set(normalized) - allowed)
    expected_posts = {
        f"_posts/{publish_date}-{'weekly-ai-oneplate' if series == 'weekly-ai' else series}.md",
        f"_posts/{publish_date}-{'weekly-ai-oneplate' if series == 'weekly-ai' else series}-en.md",
    }
    missing_posts = sorted(expected_posts - set(normalized))
    if unexpected:
        raise GateError("발행 범위 밖 파일이 staging에 포함되었습니다: " + ", ".join(unexpected))
    if missing_posts:
        raise GateError("한·영 공개 파일이 모두 staging되지 않았습니다: " + ", ".join(missing_posts))
    return normalized


def weekly_inventory(root: Path, start: str, end: str) -> list[dict[str, str]]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    results = []
    for path in sorted((root / "_posts").glob("*.md")):
        metadata = front_matter(path)
        if metadata.get("series") != "today-ai-bite":
            continue
        if metadata.get("published", "true").lower() == "false":
            continue
        if metadata.get("lang") != "ko":
            continue
        try:
            post_date = date.fromisoformat(metadata["date"])
        except (KeyError, ValueError):
            continue
        if start_date <= post_date <= end_date:
            results.append({"date": metadata["date"], "path": str(path.relative_to(root)), "title": metadata.get("title", "")})
    return results


def git_staged(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("approval", "scope", "weekly-source"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--series")
    parser.add_argument("--date")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--path", action="append", default=[], dest="paths",
                        help="scope 모드에서 검사할 staged 경로; 여러 번 지정 가능")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.mode == "approval":
            if not args.approval or not args.series or not args.date:
                parser.error("approval 모드는 --approval, --series, --date가 필요합니다.")
            validate_approval(args.approval.resolve(), root, args.series, args.date)
            print("승인 게이트 성공: schema, ko/en, 해시, front matter, permalink 확인")
        elif args.mode == "scope":
            if not args.series or not args.date:
                parser.error("scope 모드는 --series, --date가 필요합니다.")
            if args.staged and args.paths:
                raise GateError("--staged와 --path를 함께 사용할 수 없습니다.")
            paths = git_staged(root) if args.staged else args.paths
            validate_scope(paths, args.series, args.date)
            print(f"발행 범위 게이트 성공: {len(paths)}개 파일")
        else:
            if not args.start or not args.end:
                parser.error("weekly-source 모드는 --start, --end가 필요합니다.")
            items = weekly_inventory(root, args.start, args.end)
            print(json.dumps({"count": len(items), "posts": items}, ensure_ascii=False, indent=2))
        return 0
    except GateError as error:
        print(f"발행 게이트 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
