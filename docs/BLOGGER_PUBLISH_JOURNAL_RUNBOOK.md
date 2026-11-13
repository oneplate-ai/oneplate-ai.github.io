# Blogger 공개 성공 저널의 검증·보관

## 1. 적용 범위

`blogger_channel_release.py`의 `publish-*.jsonl`은 성공 후에도 다음 작업을 차단합니다. 이는 결과 불확실성을 자동 재시도로 숨기지 않기 위한 설계입니다. 이 절차는 현재 회차의 완전한 성공 저널만 재조정하고 원본 바이트를 비공개 archive에 보존합니다. Blogger 쓰기·재공개·본문 수정·저널 삭제·불확실한 저널의 자동 이동을 허용하지 않습니다. generic `reconcile-create`는 DRAFT/create 저널만 다루므로 공개 저널에 사용하지 않습니다.

운영 agent는 성공한 공개 직후 이 절차까지 같은 작업에서 수행합니다. 수동 조사나 사용자 판단이 필요한 충돌이면 보류하며 성공한 글을 재발행하지 않습니다. 정기 작업은 아래 검증을 생략하고 `mv`, 전체 폴더 비우기 또는 파일명 변경으로 잠금을 회피해서는 안 됩니다.

## 2. 정확한 저널과 승인 확인

1. 고정 상태 디렉터리는 `scripts/blogger_draft_create.py`의 `GENERIC_STATE_ROOT`입니다. 상태·journals·archive·lock에 symlink/비정규 파일을 허용하지 않습니다. 임의 상태 경로로 우회하지 않습니다.
2. 같은 `<blog_id>.lock`을 `os.open(..., O_NOFOLLOW)` 및 `fcntl.flock(LOCK_EX | LOCK_NB)`로 직접 획득해 마지막 확인과 보관까지 유지합니다. `publication_lock()`은 자기 저널도 차단하므로 재조정용으로 호출하지 않습니다. 다른 관리자의 UI/호스트 편집이 없는 유효한 단일 작성자 창을 별도로 확인합니다.
3. 현재 회차의 정확한 `publish-*.jsonl` 하나를 O_NOFOLLOW로 읽고 파일 바이트·SHA-256을 동결합니다. 첫 intent의 `blog_id`, `publication_unit`, `approval_sha256`을 현 `load_unit()`의 승인·동결 원고/이미지 맵과 대조합니다. 각 `mutation_request`에 같은 method/path/id의 `mutation_response`가 정확히 뒤따르고 response_id가 유효해야 합니다. 마지막 이벤트는 같은 블로그의 `verified`여야 합니다. 불완전·손상·다른 승인·outcome_unknown 저널은 이동하지 않습니다.
4. 다른 같은 블로그 저널 또는 소유 블로그가 불명인 저널이 있으면 중단합니다. 다른 블로그의 유효한 저널은 건드리지 않습니다. 성공 응답 ID와 intent의 기존 inventory ID를 기록하며, 생성 요청에는 응답 ID를 결속합니다. 이 단계에서 알려진 ID/키를 버리고 제목 검색으로 대체하지 않습니다.

## 3. 독립 원격 검증

5. 잠금 안에서 `BloggerChannel.client_factory(unit)`의 새 client/token과 `StrictReleaseClient.current(set(unit.sources))`로 LIVE/DRAFT/SCHEDULED를 전수 읽습니다. 한영 키가 유일하게 LIVE이며, 기존 inventory의 key→ID와 모든 확정 mutation_response ID가 현재 정확한 대상에 속해야 합니다. Preview binding이 있으면 정확 KO/EN ID·시리즈·날짜·블로그·manifest 형식도 직접 대조합니다. 바뀐 DRAFT/LIVE·이름/라벨/ID·중복·불완전 inventory는 중단합니다.
6. `channel.verify(unit, {}, {})`로 승인 본문·라벨·실제 익명 공개 URL 및 최종 변경 없음 계획을 확인합니다. 여기서는 자기 pending 저널을 거부하는 `channel.plan()`을 먼저 호출하지 않습니다. 주간은 `weekly_guard(unit, channel)`, 전후 `unit.verify_frozen()`도 적용합니다. 이 API 검증 함수는 Blogger 쓰기를 수행하지 않습니다. 이미지와 링크 검증 증거도 해당 회차 기록에 남깁니다.

## 4. 충돌 없는 증거 보관과 후속 확인

7. 원본 저널 바이트가 동결값과 여전히 같음을 확인한 후 비공개 `archive`(0700)에 같은 이름으로 `os.link(journal, destination, follow_symlinks=False)`를 실행합니다. 목적지가 이미 있으면 덮어쓰지 말고 중단합니다. archive 디렉터리를 `fsync`하고 원본/보관본의 동일 바이트·inode를 확인한 뒤에만 `os.unlink(journal)`로 journals 쪽 링크를 해제하고 journals 디렉터리도 fsync합니다. 원본 증거는 archive의 0600 파일로 남습니다. 중간 실패 시 도달 단계와 두 경로를 기록하고 맹목 재시도·증거 삭제를 금지합니다.
8. 같은 원고·승인·맵으로 새 `channel.plan(unit,{})`을 수행해 `writes=[]` 및 정확한 LIVE ID를 다시 확인합니다. 잠금을 해제한 뒤 전체 CLI 무쓰기 계획도 확인하고 회차 작업 기록에 저널 해시·archive 경로·확정 ID·독립 검증·후속 계획을 남깁니다. 이후 실패는 실제 도달 상태와 함께 보고하며 이전 공개를 자동 반복하지 않습니다.

## 5. 검증 범위

`tests/test_blogger_operations_contract.py`는 이 운영 계약과 공개 예약의 연결을 검사합니다. `scripts/test_blogger_channel_release.py`의 합성 API 검증은 실제 성공 저널·불확실한 결과 보존을 검사하며, 운영 절차 재현은 별도 비공개 합성 probe에서 같은 잠금·fresh verify·exact-ID·hard-link 보관·후속 zero-change plan을 실행합니다. 합성 통과는 실제 공개 또는 실제 저널 정리 성공을 뜻하지 않습니다.
