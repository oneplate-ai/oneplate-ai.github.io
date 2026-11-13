# Blogger 초안 미리보기 운영 절차

## 범위와 안전 조건

일간과 주간 한영 원본의 검토용 Blogger 임시보관만 만든다. 이 절차는 공개 승인이나 발행이 아니다. 공개 채널은 Blogger 하나이며 한영쌍의 승인·시간·검증 계약은 유지한다. 운영 원본은 `docs/BLOGGER_ONLY_PUBLICATION.md`다. 신규 이미지 업로드, 기존 DRAFT 수정, 테마·계정·네트워크 변경은 이 절차에 포함되지 않는다. 명시된 이미지 맵이 준비되지 않으면 미리보기 생성도 보류한다.

Blogger 관리자나 다른 호스트의 쓰기는 로컬 잠금으로 차단할 수 없다. 생성과 대조가 끝날 때까지 다른 작성자가 실제로 쓰지 않는 단일 작성자 창을 확보할 수 없으면 생성하지 않는다. 완료 상태가 불확실한 POST는 자동 재시도하지 않는다. 실패하면 모든 상태의 inventory와 저널을 대조하고 사용자에게 보고한다. 미리보기 내부 토큰 주소나 OAuth 값, 초안 전문을 로그·메시지에 싣지 않는다.

## 작성 및 생성

1. 승인 요청 대상의 정확한 한영 draft와 이미지 맵을 완성·검증한다. weekly-ai는 `blogger_weekly_evidence.py`로 실제 Blogger LIVE 일간의 새 evidence를 동결해 승인에 결합한다. exact 원본·이미지 맵의 경로와 SHA-256을 기록하며 Pages를 대조하지 않는다.
2. 운영 코드 경로는 `/root/workspaces/oneplate-ai.github.io/scripts/blogger_draft_cli.py`, 원본 경로는 `/root/oneplate-ai.github.io`다. 각 작업의 비공개 기록 디렉터리를 새로 만들고 0700으로 제한한다. 각 manifest는 새로운 절대경로를 사용한다. 같은 제목이 LIVE·DRAFT·SCHEDULED에 있거나 기존 저널이 미해결이면 보류한다. 다른 회차·기존 초안을 수정하거나 임의의 새 제목으로 우회하지 않는다.
3. `prepare-create`에 blog ID, `--ko-source`, `--en-source`, `--image-map`, `--output`을 지정해 파일을 만들고 출력된 `manifest_sha256`을 보존한다. `create`에 같은 blog ID, 기존 credential 파일의 경로, manifest와 그 해시를 지정해 먼저 무쓰기 계획을 실행한다. 두 언어가 모두 `create`이고 exact 원본 및 이미지 맵 해시가 같을 때만 동일 명령에 `--apply`를 붙여 한 번 실행한다. 모든 POST는 코드의 저널·inventory 검사와 readback을 통과해야 한다.
4. 성공 응답의 정확한 ko/en ID를 보존한다. 동일 창 안에서 CLI `reconcile-create`에 동일 `--blog-id`, `--token-path`, `--client-secret-path`, `--manifest`, `--manifest-sha256`, `--ko-id`, `--en-id`를 지정한다. 이 명령은 원격 글을 쓰지 않는다. 완료 저널의 모든 단계와 현재 두 DRAFT의 ID·상태·제목·라벨·본문을 재대조한 뒤, 해당 회차의 ID binding을 만들고 완전한 저널만 보존 archive로 옮긴다. 실패하면 재시도·이동·삭제하지 말고 원인을 보고한다. 진행 중이거나 완료되지 않은 다른 같은 블로그의 저널은 차단한다.
5. 한영 관리자 편집 URL `https://www.blogger.com/blog/post/edit/<blog-id>/<post-id>`을 사용자에게 전달한다. 이 URL은 내부 렌더 토큰 URL과 다르다. 글 상태가 양쪽 DRAFT이고 원본·이미지 맵·본문/라벨이 대조되었음을 보고한다. 사용자의 실제 계정·기기 접근이나 모바일 렌더를 확인하지 못했다면 미확인이라고 밝힌다. 이미지·출처 링크를 실제로 확인한 범위만 보고한다.
6. 사용자에게 보낼 내용에는 관리자 링크, 정확한 한영 원본 경로/SHA, 이미지 맵 경로/SHA 및 준비 상태, Blogger 단독 발행 대상과 기존 예약 19:00 KST를 포함한다. 사용자 공개 승인 전 approval JSON과 production 글을 만들지 않는다. 기존 Preview Pages에는 push하지 않는다.

## 수정 및 공개 승인

사용자가 Blogger 관리자에서 초안을 수정했다면 변경을 자동 덮어쓰지 않는다. 수정 내용을 원본에 반영하고, 필요하면 별도로 승인된 안전한 초안 update 절차로 동기화한 뒤 다시 미리보기·승인한다. 원본·이미지 맵 수정 후에도 재검토·재승인이 필요하다. 발행기는 예약 발행 전 바인딩된 정확한 ID, 승인 원본과의 본문·라벨 일치 및 미해결 저널 여부를 확인한다. ID·제목·라벨 또는 본문이 달라졌으면 해당 한영 공개 요청 전에 중단한다. DRAFT 상태 조회와 발행 사이에도 외부 사용자가 수정할 수 있으므로, 발행 시점에도 다른 작성자의 쓰기를 중지해야 한다. 두 언어의 외부 쓰기는 원자적이지 않으며 중간 장애 시 이미 적용된 결과를 보존·보고한다.

사용자의 정확한 pair 공개 승인 후 기존 `create_approval_record.py` schema v2 절차를 사용한다. weekly evidence, scheduled_publish_at 및 exact draft/image-map 해시를 결합하고 read-back 전에는 발행 준비 완료로 보고하지 않는다. 승인만으로 조기 공개하지 않는다. 기존 정시 발행 작업을 변경·복제·우회하지 않는다.
