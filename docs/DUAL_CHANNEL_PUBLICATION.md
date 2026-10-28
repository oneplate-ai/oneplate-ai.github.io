# Pages·Blogger 공통 예약 발행 계약

## 1. 범위와 승인 경계

이 문서는 기존 일간·주간·교육 연재의 발행 연결 구현 계약이다. GitHub Pages와 Blogger는 같은 승인된 한국어·영어 초안을 공유하되 서로 독립적인 발행 대상이다. Preview 호스트는 production Pages와 별개이며 Preview 배포는 공개 발행 성공이 아니다.

구현 PR은 실제 예약 변경, main 병합, 기존 누락 글 공개를 실행하지 않는다. 운영 적용은 릴리스 승인 후에만 진행한다. Blogger-only로 승인된 과거 원고의 승인이 자동으로 Pages 보충 공개 승인으로 확대되지는 않는다. 새 글 작성·본문 재작성·테마·메뉴·공개 설정·인증·자격증명 변경은 이 구현에 포함하지 않는다.

## 2. 공통 흐름

1. 정해진 초안 시각에 공식 자료 확인, exact 한영 draft 생성, Blogger image-map 준비를 진행한다.
2. 전용 `oneplate-ai/oneplate-ai-preview` 저장소에 한영 글과 해당 홈·시리즈 산출물만 배포하고 실제 원격 HTTP·SHA·noindex·canonical/hreflang·CSS를 검증한다. 사용자가 실제 보게 될 본문을 표시한다.
3. Telegram 원래 대화에 두 Preview URL과 현재 초안 SHA, 주제, 양쪽 발행 대상, 예약 시각, 이미지 준비 상태를 함께 전달한다. 실제 origin이 Telegram으로 resolve되는지 read-back한다. CLI의 origin 기본값을 Telegram이라고 가정하지 않는다.
4. 사용자가 exact pair를 승인하면 같은 대화의 후속 handler가 `scripts/create_approval_record.py`를 호출해 schema v2 승인을 즉시 materialize한다. 자연어 승인만 남고 artifact가 없는 상태를 승인 완료라고 보고하지 않는다.
5. 예약 publisher는 exact series/date로 공통 coordinator `scripts/dual_channel_release.py`를 호출한다. 기본은 읽기 전용 plan, 실제 `--apply`는 승인된 시각 이후와 운영 적용 승인 후에만 사용한다.
6. 같은 승인 파일·두 초안·image-map의 현재 해시와 scope를 양쪽 변경 전에 다시 검증한다. 두 채널을 독립 처리하며 한쪽 실패가 성공한 쪽의 롤백·삭제를 유발하지 않는다.
7. 두 채널의 한국어·영어 공개 검증을 모두 통과해야 전체 `published_verified`다. 그 전에는 채널별 상태·공개 URL·실패 단계와 남은 작업을 Telegram에 보고한다. 사용량429는 콘텐츠 승인 취소가 아니다.

## 3. 원본·승인 파일 규칙

- 편집 원본: `/root/oneplate-ai.github.io/drafts/`의 exact pair.
- 새 공통 승인 파일: `.publishing-approvals/YYYY-MM-DD-<series>.approval.json`.
- 시리즈: `today-ai-bite`, `weekly-ai`, `easy-ai`. easy episode는 draft 파일명/translation_key에 유지한다. weekly draft 파일명의 historical suffix `weekly-ai-oneplate`를 보존한다.
- 새 공통 image-map: `.publishing-approvals/YYYY-MM-DD-<series>.blogger-image-map.json`. 이미지가 없으면 helper가 `{}`를 준비한다. 이미지가 있으면 승인된 Google/Blogger media mapping이 모두 준비되어야 한다.
- 기존 `.blogger-release.json`, legacy `.json`, episode/weekly suffix 파일은 호환 resolver가 exact metadata/해시 기준으로 확인한다. 다른 원문·시각·image-map을 가리키는 후보가 함께 있으면 임의 선택하지 않고 차단한다.
- Pages와 Blogger에 서로 다른 승인 기록을 재생성하지 않는다. 한 파일을 공통으로 사용하며, source/map 변경 시 새 Preview와 재승인을 받는다.
- 승인 record와 map, 미공개 draft는 Git에 stage하지 않는다.

승인 후의 생성 명령은 다음 인자를 실제 파일에서 유도한다. 값이 불명확하면 placeholder를 실제로 실행하지 않는다.

    python3 scripts/create_approval_record.py --root /root/oneplate-ai.github.io --ko <exact-ko-draft> --en <exact-en-draft> --approval-text <사용자의-문구-원문> --approved-at <실제-승인-시각-ISO> --scheduled-publish-at <합의한-19시-KST-ISO> --blogger-image-map <준비된-map> --output <공통-approval.json>

예약 시각은 승인 답장 시각으로 당기지 않는다. 초안 요청 delivery의 follow-up handler가 이 단계를 수행해야 하며, 단순히 질문을 전달한 사실만으로 자동 연결을 검증했다고 주장하지 않는다.

## 4. 시리즈별 일정

| 시리즈 | 초안·Preview 안내(KST) | 승인된 글 발행(KST) |
|---|---|---|
| 오늘 AI 한입 | 월·수·금12:00 | 같은 날19:00 |
| 이번 주 AI 한 접시 | 금요일19:30 | 토요일19:00 |
| 쉽게 먹는 AI08 | 2026-10-22 12:00 | 같은 날19:00 |
| 쉽게 먹는 AI09 | 2026-10-29 12:00 | 같은 날19:00 |
| 쉽게 먹는 AI10 | 2026-11-05 12:00 | 같은 날19:00 |

주간 제목·집계 범위는 해당 토요일 발행일의 7일 전 토요일부터 전날 금요일까지(양끝 포함)다. 금요일 요청 시각을 기준으로 목요일까지만 집계하지 않는다. 양쪽 실제 LIVE 일간 pair 집합이 다르면 먼저 불일치를 보고하고, 근거를 섞거나 미공개 초안을 주간 근거로 사용하지 않는다. 늦은 backfill로 포함 집합이 달라지면 weekly Preview·승인을 stale 처리한다.

주간 Preview 작성 전 `python3 scripts/weekly_source_evidence.py --date <토요일-발행일> --output <비공개-review-id.evidence.json>`으로 양쪽 목록을 읽어 동일한 날짜·언어·제목 집합을 동결한다. 기존 evidence는 덮어쓰지 않는다. 이 목록·pair 수로 원고를 작성하고 Telegram Preview와 함께 제시한다. 승인 후 위 `create_approval_record.py` 명령에 `--weekly-evidence <그때-검토한-exact-evidence-file>`를 추가한다. 새 주간 승인은 이 인자 없이는 생성되지 않는다. 승인에는 `weekly_evidence: {start, end, posts: [{date, lang, title}]}`를 포함한다. 기존 주간 승인을 자동 갱신하거나 근거를 사후 끼워 넣지 않는다. 실행 시 현재 양쪽 LIVE 집합과 승인 evidence가 다르면 `weekly_evidence_mismatch`로 중단한다.

## 5. 공개·복구 안전 조건

- 기본 plan은 production 파일·Git remote·Blogger 글을 바꾸지 않는다.
- apply는 시각·승인·scope 게이트를 먼저 통과하고, 현재 operational checkout과 분리된 clean worktree에서만 Pages 원본을 만든다.
- Jekyll `_posts/` 소스만 stage하고, 홈·시리즈·sitemap은 template에서 생성한다. `update_series.py`는 legacy HTML 도구이므로 source 발행 경로에 사용하지 않는다.
- 정확한 기존 공개본과 동일하면 재커밋/재공개하지 않고 다시 read-back한다. 예상과 다른 기존 공개본은 content conflict로 중단한다.
- Pages: build·bilingual·editorial strict·scope·diff 검사 후 정상 fast-forward push만 허용한다. 원격 drift 시 force-push·자동 병합 금지. 실제 pair URL·홈·시리즈·sitemap·canonical/hreflang·CSS·이미지를 확인한다.
- Blogger: 기존 coordinator의 exact two-key scope·LIVE/DRAFT/SCHEDULED 충돌·image-map·본문 검증을 재사용한다. API/public read-back과 후속 zero-change plan을 요구한다. theme·Page·다른 pair 일괄 수정 금지.
- 재시도는 현재 두 채널을 재조회하여 실패/미확인 범위를 판단한다. 기록의 success flag만으로 공개 검증을 생략하지 않는다.

## 6. 운영 적용 준비

`docs/DUAL_CHANNEL_CRON_PROPOSAL.json`은 기존10개 정기/날짜 지정 job을 위한 proposal이다. job ID·일정·기존 Telegram origin·활성 상태를 먼저 read-back하고 `cronjob update`를 통해 승인된 prompt/skills/workdir만 갱신한다. `jobs.json`을 직접 편집하지 않는다. origin/chat ID·credential은 이 공개 proposal에 저장하지 않는다. 중복 publisher를 새로 만들지 않는다.

과거 복구 준비 사례(2026-10-19 19:00 KST): 당시 `2026-10-19-today-ai-bite.approval.json`을 수정·재승인하지 않고 resolver로 읽도록 준비했다. 이 날짜 지정 사례와 위 유한 교육 일정은 현재 회차의 새 승인이나 예약 변경 권한이 아니다. 현재 실행은 해당 회차의 정확한 승인 기록·시각·scope를 다시 확인하며, 실제 예약 적용·글 공개는 명시된 릴리스 승인을 따른다. 릴리스 승인 없이 deadline이 가까워져도 조기 공개 또는 무단 예약 변경으로 대신하지 않는다.

검증된 구현과 운영 E2E 검증은 다르다. 로컬 테스트/CI/읽기 전용 plan 통과만으로 자동 Telegram승인→19시 양쪽 공개가 실증되었다고 보고하지 않는다. 첫 승인된 정시 실행 후 채널별 공개 결과 및 Telegram 완료 전달을 별도로 기록한다.
