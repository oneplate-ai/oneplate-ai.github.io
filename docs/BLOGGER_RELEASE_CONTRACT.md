# Blogger 독립 발행 계약

## 1. 목적

Blogger는 GitHub Pages의 HTML 복제본이 아니라, 동일한 승인 원본에서 독립적으로 공개·검증되는 배포 대상입니다.

최종 원본은 승인된 한·영 `drafts/` 파일과 schema v2 승인 기록입니다. 기본 Blogger release는 Pages URL·빌드·도메인에 의존하지 않습니다. 과도기 안전장치가 필요한 운영에서는 명시적으로 `--require-pages-readback`을 지정해 Pages 공개 URL 검증을 추가할 수 있습니다.

## 2. 승인 원본과 허용 변환

하나의 Blogger release unit은 다음으로 구성됩니다.

1. schema v2 승인 기록의 한·영 초안 경로·SHA-256·제목·날짜·translation key·permalink
2. 승인된 한·영 `drafts/` 원본 자체. 발행기는 허용된 공개 상태 변환만 적용해 Blogger payload를 직접 만듭니다.
3. 승인 기록의 `blogger_image_map_sha256`과 바이트 단위로 일치하는 Blogger 이미지 URL 매핑. 매핑 대상은 HTTPS Google/Blogger 미디어 URL이어야 하며 Pages URL은 거부합니다.

GitHub Pages `_posts/`, 빌드, 공개 URL은 Blogger payload의 원본이나 기본 실행 전제조건이 아닙니다. 과도기 Pages read-back은 명시적 옵션으로 추가할 수 있습니다.

Blogger payload에는 다음 이외의 원문 변경이 허용되지 않습니다.

- `published: false`를 `published: true`로 변경
- `draft`와 `noindex` front matter 제거
- 검증 문구 블록(`<p>` 또는 `<aside>`의 `post-verification` class) 내부의 `이 초안은`/`This draft`를 `이번 게시글은`/`This post`로 변경

Blogger release는 payload가 승인 초안에서 위 허용 변환만 거쳤음을 검증한 뒤 진행합니다.

## 3. 독립 실행 순서

운영 호출은 승인 기록만 지정해 읽기 전용 계획부터 만듭니다.

```sh
python3 scripts/blogger_release.py --root /path/to/repository --approval /path/outside-git/approvals/<unit>.json --image-urls /path/to/approved-blogger-image-map.json
```

이 실행은 `_posts/` 경로를 받지 않으며, Pages URL을 기본 조회하지 않습니다. `--image-urls` 파일은 승인 기록의 `blogger_image_map_sha256`과 정확히 일치해야 합니다. 실제 쓰기는 같은 명령에 명시적으로 `--apply`를 추가할 때만 시작합니다. scheduler 연결과 자동 승인 실행은 이 계약에 포함되지 않으며 별도 단계입니다.

1. Blogger release는 `(title, language)`를 식별자로 현재 LIVE·DRAFT 글을 읽습니다.
2. 각 언어 글은 다음 중 하나만 실행합니다.
   - 없음: DRAFT 생성 후 공개
   - 동일한 DRAFT: 공개
   - 다른 DRAFT: 승인 본문으로 갱신 후 공개
   - 다른 LIVE: 같은 글을 갱신
   - 동일한 LIVE: 변경 없음
3. API read-back으로 LIVE 상태, labels, 승인 본문을 확인합니다.
4. 한·영 Blogger 공개 URL을 읽어 각각 승인 제목이 실제로 노출되는지 확인합니다.
5. 승인 쌍의 같은 언어·시리즈 목록을 계획·검증하고 기존 목록 블록은 보존합니다. 표시용 언어 nav 정책은 아래 6절을 따릅니다.
6. 과도기에만 `--require-pages-readback`으로 승인된 한·영 Pages URL의 제목 노출을 추가 검증합니다. 이 검증의 실패는 Blogger 원본이나 기존 Blogger 글을 변경하지 않습니다.

`--apply`는 각 Blogger 글을 미래 시각으로 예약하지 않습니다. 이 명령은 실행 시점에 두 언어가 모두 LIVE이며 공개 URL로 검증되는 것을 전제로 합니다. 따라서 `scheduled_publish_at`이 아직 미래라면 변경 전에 중단하며, 예약 시각 이후 동일 승인 release unit으로 실행합니다.

`--apply`가 없는 실행은 계획과 선택한 Pages read-back만 수행하며 Blogger 글을 변경하지 않습니다.

## 4. 독립성 조건

Blogger 공개 본문에는 GitHub Pages 이미지 URL을 남기지 않습니다. Pages 자산을 참조하는 `<img>`는 명시적인 Blogger URL 매핑이 없으면 차단합니다.

Blogger 공개 URL, Blogger 이미지 URL, 시리즈 목록과 표시용 언어 nav 부재는 각각 별도로 읽어 검증합니다. Pages 실패와 Blogger 실패는 같은 상태로 취급하지 않으며, 한쪽 실패가 다른 쪽의 승인 원본을 변경하거나 재생성하지 않습니다.

## 5. 운영 경계

- 새 글의 실제 공개는 명시적 `--apply`와 해당 release unit의 승인 기록이 있어야 합니다.
- `--apply`는 Blogger 공개 데이터 변경이므로 별도 공개 발행 승인에서만 사용합니다.
- 이 계약은 Pages 폐쇄, 도메인/DNS 전환, OAuth 자격증명 변경, Blogger theme/page 일괄 변경을 승인하지 않습니다.
- Pages 폐쇄는 별도 운영 결정입니다. Blogger 단독 계획 리허설이 Pages read-back 없이 성공해도, Pages 폐쇄·도메인/DNS 전환·검색 이전을 자동 승인하지 않습니다.

## 6. Blogger 표시용 언어 링크 정책 (2026-10-15)

- Blogger 본문 맨 위의 `한국어 · English` 표시용 `<nav class="oneplate-language-switcher">`는 더 이상 생성하거나 필수 검증하지 않습니다. 기존 관리 nav만 제거합니다.
- 한국어·영어 게시판 메뉴, 언어 labels, translation key, permalink, 짝글 승인과 식별·검증은 유지합니다. 본문·이미지·출처·URL·발행일·같은 언어의 시리즈 목록도 보존합니다. 이 정책은 Blogger 전용이며 GitHub Pages의 표시·hreflang 정책을 바꾸지 않습니다.
- 새 payload는 오래된 원본에 관리 nav가 있어도 이를 제외합니다. 원본 초안과 승인 해시는 수정하지 않습니다. `inject_language_switcher(...)`는 이전 호출 호환용 deprecated 별칭이며 링크를 삽입하지 않고 제거만 합니다.
- 기존 LIVE 쌍의 제거는 `remove_language_switcher` action에 정확한 `id`와 `key`로 나타납니다. 승인 본문 갱신에 이미 포함된 제거는 `update_live`의 `remove_language_switcher: true`로 표시하고 추가 쓰기로 중복 계획하지 않습니다. 새 공개 후 작업은 `update_series_navigation_after_publish`이며 언어 nav를 생성하지 않습니다.
- release/recovery는 승인된 두 글만 씁니다. corpus-wide 정리 도구를 발행 중 호출하지 않습니다. 실제 nav 변경 key 집합과 계획이 다르면 쓰기 전에 해당 단계를 중단합니다. 실패 시 `switcher_completed`는 생성 건수가 아니라 **관리 nav 제거 완료 ID**이며, 준비·공개·LIVE·시리즈 완료와 실패 대상 ID를 함께 보존합니다.

### 기존 LIVE 글·복사 Page 본문 정리용 순수 함수

```python
# 저장소 루트에서 PYTHONPATH=scripts 로 실행; 네트워크/파일 쓰기 없음
from blogger_adapter import remove_language_switcher
expected_content = remove_language_switcher(original_content)
```

이 함수는 실제 nav 요소의 정확한 `oneplate-language-switcher` class token이 있는 범위만 삭제합니다. 주변 공백·줄바꿈을 포함한 나머지 문자열은 직렬화하거나 정규화하지 않습니다. marker가 없는 본문은 byte-identical no-op이며 일반 한국어/English 문구, 다른 nav, 댓글·script 안 예시는 제거하지 않습니다. 닫히지 않은 관리 nav는 `ValueError`로 중단합니다.

운영 정리는 원격 전체 API record를 비공개로 동결한 뒤 승인된 LIVE Post 대상만 선택해 `content`만 PATCH합니다. 각 원본/예상 본문과 metadata를 보존하고 쓰기 직전에 동결본 일치 여부를 확인합니다. 응답과 새 API GET의 정확한 본문·상태·labels·URL·발행일, 시리즈 목록, 공개 URL을 따로 검증합니다. 복사 Page 본문에도 같은 순수 함수를 사용할 수 있지만 marker가 없으면 Page 메뉴·홈 링크·본문은 변경하지 않습니다. 이 함수는 외부 apply나 일괄 변경 승인을 제공하지 않습니다.
