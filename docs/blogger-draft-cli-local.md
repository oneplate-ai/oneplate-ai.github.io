# Blogger 초안 CLI 로컬 사용

이 도구는 초안 전용이며 공개하지 않습니다. 운영 사용 전 독립 재검토와 실제 서버의 조건부 갱신 동작 검증이 필요합니다.

## 생성 manifest 준비

```sh
python scripts/blogger_draft_cli.py prepare-create --blog-id BLOG_ID \
  --ko-source /absolute/path/ko.md --en-source /absolute/path/en.md \
  --image-map /absolute/path/image-map.json --output /private/path/manifest.json
```

이 명령은 원본과 이미지 맵을 읽어 manifest 파일을 새로 만들고 파일 바이트의 SHA-256을 출력합니다. 네트워크와 토큰을 사용하지 않으며 기존 파일이나 심볼릭 링크를 덮어쓰지 않습니다. 파일은 소유자 전용으로 생성됩니다. 출력된 해시와 manifest를 확인한 뒤 기존 `create --manifest ... --manifest-sha256 ...` 계획 명령에 전달하세요. 생성 계획은 Blogger 쓰기를 하지 않지만 OAuth 토큰 갱신과 원격 읽기가 발생할 수 있습니다. `--apply`는 별도 승인 없이 실행하지 마세요.

적용 전 전체 실행 동안 Blogger UI 및 다른 호스트를 포함한 외부 쓰기 작업을 중지해야 합니다. 로컬 잠금은 외부 쓰기를 막지 못하며, inventory 검사와 PUT 사이의 경합을 원천적으로 제거하지 않습니다. 오류나 중단 후에는 재시도하지 말고 저널과 원격 상태를 수동 대조하세요. 계획과 저널에는 비공개 초안 내용이 포함될 수 있습니다.


## 초안 수정 계획과 보존

`update` 계획은 원본·이미지 맵·대상 ID 외에 `--output /private/path/update-manifest.json`을 요구합니다. 본문이 포함된 계획은 덮어쓰기 없이 모드 0600 파일로 저장하고, 표준 출력에는 파일 경로와 manifest 해시만 표시합니다. 경로의 상위 디렉터리는 사전에 비공개로 준비하세요. 저장된 계획을 검토하고 해당 해시를 `--manifest-sha256`으로 지정해 명시적으로 `--apply`할 때만 수정합니다.

격리된 시험 초안 1건에서 기존 `published`를 포함한 조건부 PUT은 날짜를 보존했고, 잘못된 ETag의 PUT은 412로 거부되었습니다. 이 관찰은 모든 초안에 대한 보증이 아닙니다. update는 사전 백업한 유효한 `published`를 각 PUT에 포함하고 응답 및 재조회에서 정확히 비교합니다. PATCH는 잘못된 If-Match가 무시된 관찰이 있으므로 사용하지 않습니다. 기존 미해결 저널은 보존하고 재조정해야 하며 실제 운영 쓰기는 별도 승인과 독립 검토가 필요합니다.
