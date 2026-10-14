# Blogger 공식 채널 전환 준비 계약

## 1. 현재 판정

Blogger의 공개 URL, canonical, robots.txt, 글 sitemap, 고정 Page sitemap은 현재 Blogger 도메인에서 응답합니다.

그러나 2026-10-14 읽기 전용 검증에서 실제 공개 HTML은 저장소의 `theme-ai-oneplate.xml` 식별자를 포함하지 않았습니다. 즉, 저장소의 pilot theme는 아직 Blogger 운영 테마로 적용되지 않았습니다. 공식 채널 전환은 이 차이를 해소하기 전에는 완료로 선언할 수 없습니다.

## 2. 전환 전에 갖춰진 항목

- 58개 공개 글은 모두 Blogger 도메인으로 된 개별 한·영 counterpart switcher를 가집니다.
- API와 공개 HTML에서 Pages 도메인 참조는 확인되지 않았습니다.
- Blogger canonical은 홈, 글, label archive에서 자기 Blogger URL을 가리킵니다.
- Blogger robots.txt와 sitemap.xml, sitemap-pages.xml은 공개 응답합니다.
- 저장소 theme는 이제 Pages CSS·로고 URL을 런타임에 요청하지 않는 self-contained 후보입니다.

## 3. 남은 전환 차단 항목

1. **운영 테마 적용과 read-back**
   - self-contained theme를 Blogger에 적용한 뒤 실제 홈·글·label archive HTML에서 `theme-ai-oneplate` marker와 Pages URL 0건을 검증해야 합니다.
   - 이는 외부 Blogger theme 설정 변경이므로 별도 승인 없이는 실행하지 않습니다.

2. **한·영 SEO 선언의 운영 정책**
   - 글 본문의 reciprocal language switcher는 완료되었지만, 현재 Blogger head에는 post-level `hreflang` 선언이 없습니다.
   - Blogger 템플릿은 상대 글 URL을 기본 데이터로 제공하지 않으므로, 잘못된 전역 hreflang를 삽입하지 않습니다.
   - 전환 승인 전에 Blogger가 지원하는 안전한 per-post head metadata 방식 또는 명시적인 "언어 링크만 제공" SEO 정책을 결정해야 합니다.

3. **Pages의 역할 확정**
   - 공식 전환 전에는 Pages를 보조/아카이브 채널로 유지합니다.
   - Pages canonical·robots·삭제·리디렉션·도메인 전환은 이 문서나 theme 적용 범위에 포함되지 않습니다.

## 4. 승인 후 단위

전환 릴리스는 다음을 하나의 명시된 범위로 승인받아야 합니다.

- Blogger theme 적용
- 적용 전·후 theme export/복구본 보존
- Blogger 홈, 대표 한·영 글, 세 label archive, robots/sitemap의 API·공개 HTML read-back
- Pages를 보조 채널로 유지한다는 운영 선언

전환 릴리스에 자동으로 포함되지 않는 항목은 Pages canonical 변경, noindex, Pages 폐쇄, DNS/custom-domain, Search Console 속성 변경, 기존 URL 리디렉션입니다.
