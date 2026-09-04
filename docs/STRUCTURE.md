# AI 한 접시 구조 변환

## 2단계 기본 골격

이 프로젝트는 Docker 안에서 Jekyll을 실행합니다.

```text
콘텐츠 원본 (Markdown + YAML)
        ↓
Jekyll 템플릿·Liquid
        ↓
_site/ 정적 HTML
        ↓
GitHub Pages
```

현재 공개 HTML은 이전 단계의 결과물로 보존합니다. 다음 단계부터 공통 템플릿과 콘텐츠 이전을 진행합니다.

## 로컬 명령

```bash
docker compose build
docker compose run --rm jekyll bundle exec jekyll build
docker compose up
```

- 빌드 결과: `_site/`
- 개발 서버: `http://localhost:4000`
- `drafts/`는 공개 빌드에서 제외합니다.
- Docker와 Bundler 버전은 `Dockerfile`, `Gemfile`, `Gemfile.lock`으로 고정합니다.
