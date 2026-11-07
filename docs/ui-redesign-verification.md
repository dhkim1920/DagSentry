# DagSentry UI 리디자인 검증

2026-11-07에 로컬 데모 DB와 Chromium 145.0.7632.6, Playwright 1.58.2, macOS에서 검증했다. 실제 수정 코드는 `src/dagsentry/web/`에 있다. 디자인 규칙은 [DESIGN.md](../DESIGN.md)에 기록했다.

## 2차 화면 구조 변경

1차 변경은 기존 박스 배치를 유지해 전체 화면의 차이가 부족했다. 두 배치를 실제 데이터로 비교했다. 통계를 오른쪽에 두는 안은 목록 폭을 좁혔다. 상태 전환을 상단에 통합하는 안을 선택했다. `Exhaust the Design Space`와 `Experience First` 원칙에 따라 최종 화면의 작업 흐름을 기준으로 선택했다.

- 통계 카드를 상태 빠른 필터로 바꿨다. 검색 결과 수는 목록 제목 옆으로 옮겼다.
- 216px 사이드바와 더 큰 페이지 제목으로 내비게이션과 작업 영역의 위계를 구분했다.
- 필터와 결과를 투명한 공통 작업 영역으로 연결했다. 검색·상세 필터는 데스크톱에서 같은 줄에 놓인다. 상세 필터를 펼쳐도 검색 버튼을 가리지 않는다.
- 자동 진단의 원인을 독립 구획으로 강조하고, 반복되는 이력 카드와 오른쪽 정보 카드의 외곽선을 줄였다.
- 리포트 필터는 넓은 화면에서 검색 버튼까지 한 줄, 태블릿에서 2열, 모바일에서 1열이다.
- 빠른 상태 전환의 키보드 포커스 유지, URL·필터 복원과 실제 상세 진입 뒤 Back·Forward를 추가 검사했다. 필터끼리의 이동은 기존 `replaceState` 동작을 유지한다.

최초 UI와 최종 화면의 [주요 화면 전후 비교](ui-redesign/comparison.md)를 저장소에 포함했다. 전체 1차·2차 갤러리는 로컬 `.audit/ui-redesign-v2/comparison.html`에 보존한다.

## 변경 사항

| 파일 | 변경 |
| --- | --- |
| `web/app.css` | 공통 디자인 토큰과 Tabler 변수 |
| `web/styles/layout.css` | 내비게이션, 여백, 압축된 통계 배치 |
| `web/styles/components.css` | 타이포그래피, 버튼, 폼, 표, 상태색, 증거 로그 |
| `web/styles/views.css` | 집계 설명, 상세 상단 대응 영역, 근거·조치 배치, 이력 구분, 모바일 헤더 |
| `web/index.html` | 기존 상태 변경 컨트롤을 상세 상단에 배치, 영문 원문과 번역 연결 |
| `web/views/incidents/detail.js` | 최신 검증 진단의 원인과 실제 근거·조치를 함께 표시 |
| `web/views/incidents/list.js` | 상태 빠른 필터와 기존 URL·검색 상태 연결 |
| `web/views/diagnoses.js` | 검증 결과와 출처에 따른 행 표시용 데이터 속성 |
| `web/components/diagnosis.js` | 증거 로그의 키보드 접근 |
| `web/components/badges.js`, `web/core/i18n.js`, `web/i18n/ko.js` | 진단 출처·상태와 동적 문구의 양방향 언어 전환 |
| `web/views/reports.js`, `web/views/signatures.js`, `web/views/incidents/human-diagnosis.js` | 기존 한글 고정 문구를 번역 체계에 연결 |
| `tests/test_web_ui.py` | 의도적으로 변경된 자산 버전·문구·컨트롤 위치 기대값 갱신 |
| `scripts/verify-ui-tabler.cjs` | 운영자 진단 영문 표시와 긴 증거의 키보드 스크롤 검증 |
| `scripts/verify-ui-redesign.cjs` | 동일 데이터 전후 캡처, 실제 근거 내용, 버튼 대비, 성공 피드백 검증 |

## 화면 검토

로컬 `.audit/ui-redesign-v2/comparison.html`에 9개 화면의 한국어·영어, 1440·768·390·320px 캡처를 나란히 배치했다. 저장소에는 주요 데스크톱 화면과 목록·상세의 모바일 전후 캡처 22장을 포함했다. 변경 전 72장과 변경 후 72장이다. 별도의 회귀 캡처에는 로그인, 보고서 상세 fixture, 확인 대화상자, 오류와 긴 문자열 등이 있다.

| 단계 | 화면과 판정 | 관찰과 변경 |
| --- | --- | --- |
| 1 | Incident 목록, 검증 통과 | 집계 설명의 별도 박스를 줄여 목록을 위로 올렸다. 오류명과 메시지·Task의 위계를 분리했다. 모바일은 기존 모든 열을 레이블이 있는 행으로 유지한다. |
| 2 | Incident 상세, 검증 통과 | 기존에는 근거가 펼침 안에 있고 대응 버튼이 진단 아래에 있었다. 대응은 상단, 실제 근거와 조치는 원인 바로 아래로 이동했다. 자동 검증과 운영자 확정의 차이를 명시했다. |
| 3 | Signature 목록, 검증 통과 | 오류와 보조 설명의 글꼴 위계, 빈도 숫자, 표 간격과 상세 진입의 일관성을 맞췄다. |
| 4 | Signature 상세, 검증 통과 | 기존 실제 데이터 차트와 7일·30일 탐색을 유지했다. 추이색·격자·구획을 통일하고 발생 시각의 과도한 줄바꿈을 줄였다. |
| 5 | Diagnosis 이력, 검증 통과 | 출처·검증 결과는 별도 열이다. 거부·철회와 운영자 확정·수정은 텍스트와 시작선으로 구별한다. 영문 모드에서 한글로 남던 배지를 수정했다. |
| 6 | Diagnosis 상세, 검증 통과 | 원인·근거·조치·관련 Incident·메타데이터에 같은 디자인 규칙을 적용했다. 원래의 출처와 검증 안내는 유지한다. |
| 7 | Daily Reports, 검증 통과 | 통계·필터·빈 결과와 fixture 보고서 상세의 일관성을 확인했다. 실제 데모 DB에는 전송된 보고서가 없다. |
| 8 | Settings, 검증 통과 | 사용자·연결·운영 상태·감사·실패 작업의 공통 스타일과 역할 제한을 기존 검증으로 확인했다. |
| 9 | Report settings, 검증 통과 | 필드·토글·도움말·최근 실행을 통일했다. 설정과 상태 피드백의 한글 고정 문구를 번역 체계로 옮겼다. 입력된 제목은 번역하지 않는다. |

전후 캡처는 동일한 데모 DB를 사용한다. 캡처 스크립트가 Incident API 응답 전체의 동등성을 검사한다. 로그인마다 바뀌는 사용자 최근 로그인 시각과 같은 세션 메타데이터는 고정하지 않았다. 운영 DB·고객 데이터는 사용하지 않았다.

## 실행한 검증

| 검증 | 결과 |
| --- | --- |
| `.venv/bin/pytest -q` | 724 passed, 37 skipped, 19.69초 |
| `scripts/verify-ui-tabler.cjs` | PASS, Chromium 145.0.7632.6 |
| `scripts/verify-ui-redesign.cjs before` | 72장 캡처 |
| `scripts/verify-ui-redesign.cjs after` | 72장 캡처, Incident 데이터 동등성, 실제 근거 원문 일치, 주요 내용의 첫 뷰포트 진입, 상태 변경 성공 피드백 통과 |
| `.venv/bin/ruff check tests/test_web_ui.py` | PASS |
| `git diff --check` | PASS |
| 원본과 현재 HTML의 ID·폼 훅 비교 | 371개 ID와 62개 필드 훅 동일 |
| vendor diff 및 기존 자산 해시 테스트 | 변경 없음, PASS |

전체 브라우저 검증은 1440·1024·768·390·320px에서 실행한다. 별도 전후 캡처는 1440·768·390·320px의 한국어·영어를 모두 검사한다. 페이지 전체의 가로 넘침이 없어야 한다. 이름이 있는 표 영역 내부의 가로 스크롤은 허용한다.

기존 브라우저 검증에는 다음 항목이 포함된다.

- 로그인 오류와 로그아웃, 임시 비밀번호, Viewer의 관리 화면·대응 컨트롤 접근 제한.
- 필터·정렬·페이지 이동·상세 진입·뒤로 가기·앞으로 가기·URL 재로드.
- 한국어·영어 전환과 시간대 설정 복원.
- native dialog의 포커스·Escape, skip link, 표 키보드 스크롤, 긴 증거 키보드 스크롤.
- 최신 운영자 진단 우선 표시, 거부된 자동 진단의 이력 보존.
- 로딩·빈 결과·요청 오류, 긴 문자열·XSS 공격 문자열의 텍스트 표시.
- 상태 변경과 설정 저장 요청의 CSRF 헤더, CSP 위반·외부 런타임 요청·JavaScript 오류 없음.
- 기존 추이 차트의 7일·30일, 키보드 탐색, 부수 요청 실패와 복원.
- 720px reflow, reduced motion, forced-colors 에뮬레이션.

주요 버튼의 실제 computed color로 측정한 명암비는 기본 5.78:1, hover 5.17:1이다. 작은 텍스트의 4.5:1 기준을 검사한다. 화면 전체가 접근성 기준을 충족한다는 판정은 아니다.

독립 검토에서 발견한 버튼 대비와 운영자 출처 번역을 수정했다. 추가 설명용 CSS 주석 1개를 삭제했다. 기능을 바꾸지 않는 최소 변경을 택한 `Laziness Protocol`과 실제 결과로 확인하는 `Prove It Works` 원칙을 적용했다.

기계 판독 결과와 원본 로그는 다음 파일에 있다. 결과 JSON의 캡처 디렉터리와 결정 기록의 `.audit/` 경로는 검증 당시 로컬 산출물 위치다.

- [브라우저 검증 report.json](ui-redesign/results/browser.json).
- [캡처·동일 데이터·버튼 대비 결과](ui-redesign/results/capture.json).
- [전체 pytest 결과](ui-redesign/results/pytest.txt).
- [결정 기록](ui-redesign/decisions.tsv).

## 재현

Playwright는 테스트 전용이다. 프로젝트 런타임 의존성이나 프로덕션 빌드에 추가하지 않았다. 아래 경로는 이번 세션의 임시 설치 경로다.

```sh
.venv/bin/python scripts/run-ui-demo.py --port 8011
```

별도 터미널에서 변경 전 코드를 캡처하고, UI 수정 후 동일한 DB로 변경 후를 캡처한다. DB를 초기화하면 식별자가 바뀌므로 비교 검증에 실패한다.

```sh
env NODE_PATH=/private/tmp/dagsentry-redesign-browser/node_modules \
  PLAYWRIGHT_BROWSERS_PATH=/private/tmp/dagsentry-redesign-browser/browsers \
  node scripts/verify-ui-redesign.cjs before
env NODE_PATH=/private/tmp/dagsentry-redesign-browser/node_modules \
  PLAYWRIGHT_BROWSERS_PATH=/private/tmp/dagsentry-redesign-browser/browsers \
  node scripts/verify-ui-redesign.cjs after
env NODE_PATH=/private/tmp/dagsentry-redesign-browser/node_modules \
  PLAYWRIGHT_BROWSERS_PATH=/private/tmp/dagsentry-redesign-browser/browsers \
  DAGSENTRY_UI_TEST_URL=http://127.0.0.1:8011 \
  DAGSENTRY_UI_SCREENSHOTS=.audit/ui-redesign-v2/regression \
  node scripts/verify-ui-tabler.cjs
.venv/bin/pytest -q
```

## 미검증 범위와 남은 제약

- PostgreSQL·Ollama 통합 테스트 37개는 외부 서비스 환경변수가 없어 건너뛰었다. SQLite 기반 API·권한·보안 테스트는 실행했다.
- 실제 Safari·Firefox, OS 고대비, 실제 브라우저 200% 확대, VoiceOver·NVDA는 실행하지 않았다.
- Viewer·임시 비밀번호·긴 데이터·운영자 확정 진단·보고서 상세·쓰기 거부는 브라우저 응답 fixture로 검사한 항목이 있다. 역할별 서버 차단은 pytest로 별도 검사했다.
- 상태 변경 성공 UI는 응답 fixture로 검사했다. 실제 운영 Incident를 변경하거나 알림을 발송하지 않았다. 상태 변경 서버 동작은 기존 pytest에 포함된다.
- 저밀도 데모 데이터와 긴 문자열 fixture를 사용했다. 실제 대규모 운영 데이터에서의 작업 속도와 운영자 사용성 인터뷰는 수행하지 않았다.
- 내장 브라우저 실행 도구가 없어 저장소의 standalone Playwright를 사용했다. Sentry 및 레퍼런스 서비스의 로그인 뒤 화면을 직접 검토했다고 주장하지 않는다.
