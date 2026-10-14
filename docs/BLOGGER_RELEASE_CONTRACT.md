# Blogger 독립 발행 계약

## 1. 목적

Blogger는 GitHub Pages의 HTML 복제본이 아니라, 동일한 승인 원본에서 독립적으로 공개·검증되는 배포 대상입니다.

최종 원본은 승인된 한·영 `drafts/` 파일과 schema v2 승인 기록입니다. 기본 Blogger release는 Pages URL·빌드·도메인에 의존하지 않습니다. 과도기 안전장치가 필요한 운영에서는 명시적으로 `--require-pages-readback`을 지정해 Pages 공개 URL 검증을 추가할 수 있습니다.

## 2. 승인 원본과 허용 변환

하나의 Blogger release unit은 다음으로 구성됩니다.

1. schema v2 승인 기록의 한·영 초안 경로·SHA-256·제목·날짜·translation key·permalink
2. 승인된 한·영 `drafts/` 원본 자체. 발행기는 허용된 공개 상태 변환만 적용해 Blogger payload를 직접 만듭니다.
3. Blogger 이미지 URL 매핑

GitHub Pages `_posts/`, 빌드, 공개 URL은 Blogger payload의 원본이나 기본 실행 전제조건이 아닙니다. 과도기 Pages read-back은 명시적 옵션으로 추가할 수 있습니다.

Blogger payload에는 다음 이외의 원문 변경이 허용되지 않습니다.

- `published: false`를 `published: true`로 변경
- `draft`와 `noindex` front matter 제거
- 검증 문구의 `이 초안은`/`This draft`를 `이번 게시글은`/`This post`로 변경

Blogger release는 payload가 승인 초안에서 위 허용 변환만 거쳤음을 검증한 뒤 진행합니다.

## 3. 독립 실행 순서

운영 호출은 승인 기록만 지정해 읽기 전용 계획부터 만듭니다.

```sh
python3 scripts/blogger_release.py --root /path/to/repository --approval /path/outside-git/approvals/<unit>.json --image-urls /path/to/approved-blogger-image-map.json
```

이 실행은 `_posts/` 경로를 받지 않으며, Pages URL을 기본 조회하지 않습니다. 실제 쓰기는 같은 명령에 명시적으로 `--apply`를 추가할 때만 시작합니다. scheduler 연결과 자동 승인 실행은 이 계약에 포함되지 않으며 별도 단계입니다.

1. Blogger release는 `(title, language)`를 식별자로 현재 LIVE·DRAFT 글을 읽습니다.
2. 각 언어 글은 다음 중 하나만 실행합니다.
   - 없음: DRAFT 생성 후 공개
   - 동일한 DRAFT: 공개
   - 다른 DRAFT: 승인 본문으로 갱신 후 공개
   - 다른 LIVE: 같은 글을 갱신
   - 동일한 LIVE: 변경 없음
3. API read-back으로 LIVE 상태, labels, 승인 본문을 확인합니다.
4. 한·영 Blogger 공개 URL을 읽어 각각 승인 제목이 실제로 노출되는지 확인합니다.
5. 한·영 상호 링크는 Blogger가 반환한 공개 URL을 사용합니다. 이미 있는 시리즈 목록 블록은 보존합니다.
6. 과도기에만 `--require-pages-readback`으로 승인된 한·영 Pages URL의 제목 노출을 추가 검증합니다. 이 검증의 실패는 Blogger 원본이나 기존 Blogger 글을 변경하지 않습니다.

`--apply`는 각 Blogger 글을 미래 시각으로 예약하지 않습니다. 이 명령은 실행 시점에 두 언어가 모두 LIVE이며 공개 URL로 검증되는 것을 전제로 합니다. 따라서 `scheduled_publish_at`이 아직 미래라면 변경 전에 중단하며, 예약 시각 이후 동일 승인 release unit으로 실행합니다.

`--apply`가 없는 실행은 계획과 선택한 Pages read-back만 수행하며 Blogger 글을 변경하지 않습니다.

## 4. 독립성 조건

Blogger 공개 본문에는 GitHub Pages 이미지 URL을 남기지 않습니다. Pages 자산을 참조하는 `<img>`는 명시적인 Blogger URL 매핑이 없으면 차단합니다.

Blogger 공개 URL, Blogger 이미지 URL, 한·영 링크, 시리즈 목록은 각각 별도로 읽어 검증합니다. Pages 실패와 Blogger 실패는 같은 상태로 취급하지 않으며, 한쪽 실패가 다른 쪽의 승인 원본을 변경하거나 재생성하지 않습니다.

## 5. 운영 경계

- 새 글의 실제 공개는 명시적 `--apply`와 해당 release unit의 승인 기록이 있어야 합니다.
- `--apply`는 Blogger 공개 데이터 변경이므로 별도 공개 발행 승인에서만 사용합니다.
- 이 계약은 Pages 폐쇄, 도메인/DNS 전환, OAuth 자격증명 변경, Blogger theme/page 일괄 변경을 승인하지 않습니다.
- Pages 폐쇄는 별도 운영 결정입니다. Blogger 단독 계획 리허설이 Pages read-back 없이 성공해도, Pages 폐쇄·도메인/DNS 전환·검색 이전을 자동 승인하지 않습니다.
