#!/usr/bin/env python3
"""Prepare a safe Blogger image-map artifact for one bilingual draft pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from blogger_adapter import build_blogger_payload
from blogger_release import _image_urls_bytes, pages_image_sources, public_copy_from_draft


class ImageMapPreparationError(ValueError):
    """A draft pair cannot safely receive a Blogger image-map artifact."""


def _image_keys(draft_path: Path) -> dict[str, str]:
    public_text = public_copy_from_draft(draft_path.read_text(encoding="utf-8"))
    payload = build_blogger_payload(
        public_text,
        public_base_url="https://oneplate-ai.github.io",
        draft=False,
    )
    sources = pages_image_sources(str(payload["content"]))
    keys: dict[str, str] = {}
    for canonical in sources.values():
        parsed_path = urlparse(canonical).path
        prefix = "/assets/images/"
        if not parsed_path.startswith(prefix):
            raise ImageMapPreparationError(f"Blogger 이미지 mapping source가 assets/images 아래가 아닙니다: {canonical}")
        key = parsed_path.removeprefix(prefix)
        if not key or Path(key).is_absolute() or ".." in Path(key).parts:
            raise ImageMapPreparationError(f"Blogger 이미지 mapping 키를 만들 수 없습니다: {canonical}")
        previous = keys.setdefault(key, canonical)
        if previous != canonical:
            raise ImageMapPreparationError(
                f"서로 다른 이미지가 같은 mapping 키를 사용합니다: {key} ({previous}, {canonical})"
            )
    return keys


def prepare_image_map(root: Path, ko: Path, en: Path, output: Path) -> dict[str, Any]:
    root = root.resolve()
    pair = {"ko": ko.resolve(), "en": en.resolve()}
    for language, path in pair.items():
        if not path.is_file() or root not in path.parents:
            raise ImageMapPreparationError(f"{language} 초안은 root 내부의 파일이어야 합니다: {path}")
    expected: dict[str, str] = {}
    for path in pair.values():
        for key, canonical in _image_keys(path).items():
            previous = expected.setdefault(key, canonical)
            if previous != canonical:
                raise ImageMapPreparationError(
                    f"한·영 초안의 이미지 mapping 키가 서로 다릅니다: {key} ({previous}, {canonical})"
                )
    required_keys = sorted(expected)
    output = output.resolve()
    if not required_keys:
        if output.exists():
            existing = json.loads(output.read_text(encoding="utf-8"))
            if existing != {}:
                raise ImageMapPreparationError(f"이미지가 없는 release unit의 image map은 비어 있어야 합니다: {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("{}\n", encoding="utf-8")
        return {"status": "ready_no_images", "image_map_path": str(output), "required_image_keys": []}
    if not output.exists():
        return {
            "status": "mapping_required",
            "image_map_path": str(output),
            "required_image_keys": required_keys,
            "source_images": [expected[key] for key in required_keys],
        }
    try:
        supplied = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ImageMapPreparationError(f"Blogger image map JSON을 읽을 수 없습니다: {output}") from error
    if not isinstance(supplied, dict) or set(supplied) != set(required_keys):
        raise ImageMapPreparationError(
            f"Blogger image map 키가 승인 초안과 일치하지 않습니다: expected={required_keys}, actual={sorted(supplied) if isinstance(supplied, dict) else 'non-object'}"
        )
    _image_urls_bytes(output.read_bytes())
    return {"status": "ready_existing_map", "image_map_path": str(output), "required_image_keys": required_keys}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ko", type=Path, required=True)
    parser.add_argument("--en", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare_image_map(args.root, args.ko, args.en, args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeDecodeError, ValueError, ImageMapPreparationError) as error:
        print(f"Blogger image map 준비 실패: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
