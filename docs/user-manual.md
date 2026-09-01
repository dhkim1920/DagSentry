# DagSentry Web UI 사용자 매뉴얼

이 문서는 DagSentry 웹 UI에서 Incident를 확인하고, Error Signature와 Diagnosis를 조사하며,
관리자가 사용자와 외부 연결을 관리하는 방법을 설명한다.

문서 기준일: 2026-08-26

## 1. 화면에서 사용하는 개념

DagSentry는 다음 세 개념을 서로 다른 화면으로 구분한다.

- **Incident**: 운영자가 확인하고 상태를 변경하는 장애 대응 단위
- **Error Signature**: 동일한 실패 패턴을 묶는 결정적 식별 단위
- **Diagnosis**: Rule, AI 또는 기존 결과 재사용으로 생성된 원인 분석 기록

일반적인 조사 순서는 `Incident → Error Signature → Diagnosis`다.

## 2. 역할과 권한

| 기능 | Viewer | Operator | Admin |
| --- | --- | --- | --- |
| Incident, Error Signature, Diagnosis 조회 | 가능 | 가능 | 가능 |
| Incident 상태 변경 | 불가 | 가능 | 가능 |
| 사용자 및 세션 관리 | 불가 | 불가 | 가능 |
| Managed Connection 관리 | 불가 | 불가 | 가능 |
| Admin 감사 이력 조회 | 불가 | 불가 | 가능 |

Admin 메뉴는 Admin 계정으로 로그인했을 때만 표시된다.

## 3. 접속과 로그인

### 3.1 운영 환경

브라우저에서 관리자가 안내한 DagSentry 주소를 연다. `/`로 접속하면 `/ui/`로 이동한다.

1. 이메일과 비밀번호를 입력한다.
2. **로그인**을 누른다.
3. 로그인 요청 중에는 화면 중앙에 로딩 표시가 나타난다.
4. 임시 비밀번호를 사용하는 계정은 먼저 새 비밀번호를 설정한다.

운영 설치에는 기본 이메일이나 기본 비밀번호가 없다. 계정은 DagSentry 관리자가 생성한다.

### 3.2 로컬 데모 실행

프로젝트 의존성을 설치한 후 다음 명령을 실행한다.

```shell
uv sync
uv run python scripts/run-ui-demo.py --reset
```

접속 정보:

```text
URL:      http://127.0.0.1:8000/ui/
Email:    admin@dagsentry.local
Password: DagSentry-demo-2026!
```

이 계정은 로컬 데모 전용이며 운영 환경에서 사용하면 안 된다.

`--reset`은 `/tmp/dagsentry-ui-demo.sqlite3` 데모 데이터베이스를 새로 생성한다. 기존 데모
상태를 유지하려면 다음 실행부터 `--reset`을 제외한다.

같은 로컬 네트워크의 다른 장비에서 확인하려면 다음과 같이 실행한다.

```shell
uv run python scripts/run-ui-demo.py --host 0.0.0.0
```

다른 장비에서는 `http://<DagSentry를 실행한 장비의 IP>:8000/ui/`로 접속한다. 운영체제
방화벽이 8000번 포트 연결을 허용해야 한다. 데모 계정은 신뢰할 수 있는 로컬 네트워크에서만
사용한다.

Daily Report의 **지금 실행**과 자동 실행도 함께 확인하려면 `--with-scheduler`를 추가한다.

```shell
uv run python scripts/run-ui-demo.py --host 0.0.0.0 --with-scheduler
```

이 옵션은 UI 서버와 같은 데모 SQLite DB를 사용하는 스케줄러를 시작하고, UI 서버를 종료하면
스케줄러도 함께 종료한다. Daily Report 알림은 현재 환경의 알림 설정을 사용하므로, 외부 전송
설정을 사용 중이라면 실제 알림이 전송될 수 있다.

## 4. 공통 화면 사용법

### 4.1 좌측 메뉴

로그인 후 다음 메뉴가 좌측에 표시된다.

- **Incidents**
- **Error Signatures**
- **Diagnosis History**
- **Admin**: Admin 전용

좌측 메뉴 하단의 화살표 버튼으로 메뉴를 접거나 펼친다. 메뉴를 접어도 화살표를 다시 누를 수
있도록 얇은 메뉴 레일은 남는다. 로그인 전에는 좌측 메뉴와 레일이 모두 표시되지 않는다.

### 4.2 언어와 시간대

상단 우측에서 다음 값을 선택할 수 있다.

- **Language**: 한국어 또는 English
- **Timezone**: Asia/Seoul, UTC 또는 Browser local

목록 화면은 선택한 시간대를 기준으로 표시한다. 상세 화면은 조사에 필요한 경우 선택 시간대와
UTC를 함께 보여준다. 언어와 시간대 선택은 현재 브라우저에 저장된다.

### 4.3 목록과 필터

- **검색**을 누르면 현재 필터로 목록을 다시 조회한다.
- **초기화**를 누르면 해당 화면의 필터와 페이지 위치를 초기화한다.
- **이전/다음**으로 페이지를 이동한다.
- 필터, 정렬, 페이지 상태는 URL에 반영된다. URL을 공유해도 조회 권한이 있는 로그인이 필요하다.
- 작은 화면에서 표가 화면보다 넓으면 표 영역을 가로로 스크롤한다.

### 4.4 로그아웃

상단 우측의 **로그아웃**을 누르면 현재 세션이 폐기되고 로그인 화면으로 돌아간다.

## 5. Incidents

Incidents는 운영자가 장애 대응을 시작하는 기본 화면이다.

### 5.1 목록 조회

상단 요약에서 현재 조건에 맞는 Incident 수와 OPEN, ACKNOWLEDGED 수를 확인한다.

사용 가능한 필터:

- 상태
- 환경
- DAG
- Task
- 정렬 기준
- 오름차순 또는 내림차순

**새로고침**은 현재 필터를 유지한 채 목록을 다시 조회한다.

목록에서 먼저 상태 배지를 확인한 다음 환경, DAG/Task, Error Signature, Failure 수와 발생
시각을 확인한다. 행의 **상세** 버튼을 누르면 Incident 상세로 이동한다.

### 5.2 상태 의미

| 상태 | 의미 |
| --- | --- |
| `OPEN` | 새로 생성되어 대응이 필요한 Incident |
| `ACKNOWLEDGED` | 운영자가 확인하고 조사 중인 Incident |
| `RECOVERED` | 시스템이 복구를 감지했으며 운영 종료 판단이 남은 Incident |
| `RESOLVED` | 운영 처리가 완료된 Incident |
| `IGNORED` | 추가 운영 작업 없이 종료한 Incident |

`RESOLVED`와 `IGNORED`는 종료 상태다.

### 5.3 Incident 상세 조사

상세 화면에서 다음 순서로 확인한다.

1. 현재 상태와 환경
2. Failure 횟수, 최초 발생, 최근 활동
3. Failure 및 Diagnosis 이력
4. 상태 변경 이력
5. Incident ID와 상관관계 정보

Failure의 Diagnosis 카드에서는 Source, Classification, Confidence, Root Cause, Evidence와
권장 조치를 확인할 수 있다. Error Signature가 있는 경우 해당 상세 화면으로 이동할 수 있다.

Evidence 왼쪽의 숫자는 원본 AI 입력 로그의 `lineId`다. 로그 근거를 정확히 대조하기 위한
식별자이며 순번이 아니다.

### 5.4 Incident 상태 변경

Operator 또는 Admin은 상세 화면에서 상태를 변경할 수 있다.

| 현재 상태 | 가능한 작업 |
| --- | --- |
| `OPEN` | 확인, 해결, 무시 |
| `ACKNOWLEDGED` | 해결, 무시 |
| `RECOVERED` | 해결, 무시 |
| `RESOLVED`, `IGNORED` | 변경 불가 |

작업 버튼을 누르면 확인 창이 열린다. 필요한 경우 사유를 입력한 뒤 확정한다. 모든 실제 상태
변경은 사용자, 이전/다음 상태, 시각, 사유와 함께 감사 이력에 저장된다.

동시에 다른 운영자가 상태를 변경했다면 화면이 최신 상태를 다시 불러오고 기존 작업을 덮어쓰지
않는다.

## 6. Error Signatures

Error Signatures는 반복되는 동일 오류 패턴을 찾는 화면이다. 이름이 같아 보여도 Exception,
Normalized Message, Vendor Code 또는 Operator가 다르면 서로 다른 Signature일 수 있다.

### 6.1 목록 조회

사용 가능한 필터:

- Exception, Vendor Code 또는 Normalized Message 검색
- Classification
- 환경
- DAG
- Task
- 발생 시작일과 종료일
- 최근 발생, Failure 수, Incident 수 또는 생성 시각 정렬

목록에서 Signature 식별 정보, Operator/Exception, Incident 수, Failure 수, 최초/최근 발생을
확인한다. **탐색**을 누르면 Signature 상세로 이동한다.

### 6.2 Signature 상세

상세 화면에는 다음 정보가 표시된다.

- Failure 및 Incident 수
- 최초 발생 및 최근 발생 시각
- 7일 또는 30일 발생 추이
- 연결된 Failure Try와 Incident
- 최신 검증 Diagnosis
- Fingerprint, 버전, Operator, Exception, Vendor Code, Stack Frame

발생 추이는 데이터가 없는 날짜도 0건으로 표시한다. **7일** 또는 **30일**을 누르면 해당 기간을
다시 조회한다. 작은 화면에서 30일 그래프가 넓으면 그래프 아래의 가로 스크롤을 사용한다. 그래프를
처음 열거나 기간을 변경하면 최신 날짜가 보이도록 스크롤이 우측 끝에 위치한다.

Occurrences 표의 Incident 링크를 사용하면 해당 장애 대응 화면으로 이동할 수 있다.

## 7. Diagnosis History

Diagnosis History는 생성된 진단의 출처와 검증 결과를 확인하는 화면이다.

### 7.1 Source와 Validation

| 값 | 의미 |
| --- | --- |
| `RULE` | 결정론적 규칙으로 생성된 Diagnosis |
| `AI` | AI Provider가 생성한 Diagnosis |
| `REUSED` | 검증된 기존 Diagnosis 내용을 재사용한 기록 |
| `PASSED` | 검증을 통과한 Diagnosis |
| `REJECTED` | 검증을 통과하지 못한 AI 시도 |

`REJECTED` 결과는 유효 Root Cause로 사용되거나 이후 Diagnosis 재사용의 원본으로 사용되지 않는다.

### 7.2 목록 조회

사용 가능한 필터:

- Source
- Validation 상태
- Classification
- Error Signature ID
- Failure Event ID
- 생성 시작일과 종료일
- 생성 시각 또는 Confidence 정렬

목록에서 Source, Classification, Root Cause 요약, Confidence, Validation, 생성 시각과 연결된
Signature를 확인한다. **상세**를 누르면 Diagnosis 상세로 이동한다.

### 7.3 Diagnosis 상세

다음 순서로 내용을 확인한다.

1. Source, Validation, Confidence, Retry, Review 상태
2. Root Cause
3. Evidence
4. Recommended Actions
5. Validation errors가 있는 경우 오류 내용
6. Relevant Log 링크
7. 동일 Signature의 Similar Diagnosis
8. Failure Try context

우측 Metadata에서 Diagnosis ID, Failure Event ID, Signature ID와 Schema, Prompt, Rule 버전 등
출처 정보를 확인한다. 긴 ID는 상세 화면의 복사 버튼으로 복사한다. Linked Incident와 Diagnosis
관계 링크를 사용해 관련 화면으로 이동할 수 있다.

Relevant Log는 전체 로그를 DagSentry에 복사해 보여주는 기능이 아니다. 보관된 안전한 링크를 통해
Airflow의 해당 로그로 이동한다.

## 8. Admin

Admin 메뉴는 Admin 계정에서만 사용할 수 있다.

### 8.1 사용자 생성

다음 값을 입력하고 **사용자 생성**을 누른다.

- 이메일
- 표시 이름
- 역할: Viewer, Operator 또는 Admin
- 12자 이상의 임시 비밀번호

새 사용자는 최초 로그인 후 개인 비밀번호를 설정해야 한다. 비밀번호는 화면이나 API에서 다시
조회할 수 없으므로 안전한 채널로 사용자에게 전달한다.

### 8.2 사용자 관리

Users 표에서 다음 작업을 수행할 수 있다.

- 역할 변경
- 계정 비활성화 또는 활성화
- 임시 비밀번호 재설정
- 모든 기존 세션 폐기

마지막 활성 Admin을 비활성화하거나 Admin 역할에서 내리는 작업은 거부된다. 사용자는 삭제하지
않으며 변경 이력은 Admin audit events에 남는다.

### 8.3 Managed Connection 생성

Managed Connection은 DagSentry가 외부 시스템에 접속할 때 사용하는 설정이다. Airflow 자체의
Connection 객체와는 다르다.

현재 UI에서 생성과 수정을 지원하는 Provider:

| Provider | 주요 입력값 | Secret |
| --- | --- | --- |
| Airflow | 환경, 표시 이름, API base URL, 선택적 UI base URL | Token |
| Ollama | 환경, 표시 이름, API base URL, Model | 없음 |
| Slack | 환경, 표시 이름, API base URL, Channel ID | Bot token |

Secret 입력값은 쓰기 전용이다. 기존 연결을 수정할 때 Secret을 비워두면 저장된 Secret이 유지된다.
새 값을 입력하면 기존 Secret을 교체한다. 화면은 Secret 원문을 다시 표시하지 않는다.

연결이 아직 없으면 연결 목록 표 대신 빈 상태 메시지가 표시된다. 연결을 저장한 후 목록에서 다음
작업을 사용할 수 있다.

- **수정**: 일반 설정 변경 및 필요한 경우 Secret 교체
- **테스트**: Provider별 읽기 전용 연결 확인
- **비활성화**: 런타임 사용 중지

연결 테스트는 Airflow 버전, Ollama 모델 목록 또는 Slack 계정과 Channel 접근을 확인한다. Slack
테스트는 메시지를 전송하지 않으며 Ollama 테스트는 AI 생성을 실행하지 않는다.

### 8.4 Admin 감사 이력

Admin audit events 표에서 사용자와 연결 변경의 시각, 수행자, 작업, 대상과 요약을 확인한다.
비밀번호, Token, 암호화 키 또는 Provider 응답 본문은 감사 요약에 저장되지 않는다.

## 9. 권장 운영 흐름

1. **Incidents**에서 `OPEN` 상태와 최근 활동 순으로 장애를 확인한다.
2. Incident 상세에서 Failure 이력과 유효 Diagnosis의 Root Cause 및 Evidence를 검토한다.
3. 조사 시작을 표시해야 하면 Incident를 `ACKNOWLEDGED`로 변경한다.
4. 반복 여부를 확인하려면 연결된 **Error Signature**로 이동한다.
5. 과거 분석과 검증 출처가 필요하면 **Diagnosis History** 또는 최신 검증 Diagnosis를 확인한다.
6. 조치가 완료되면 Incident를 `RESOLVED`로 변경하고 필요한 사유를 남긴다.
7. 추가 작업이 불필요한 장애만 명확한 판단 후 `IGNORED`로 종료한다.

## 10. 문제 해결

### 로그인이 되지 않음

- 이메일과 비밀번호를 다시 확인한다.
- 임시 비밀번호가 재설정되었다면 기존 세션과 비밀번호는 사용할 수 없다.
- 계정이 비활성화되었거나 세션이 만료되었을 수 있으므로 Admin에게 문의한다.
- 운영 환경은 HTTPS 주소를 사용하는지 확인한다.

### 목록에 데이터가 없음

- **초기화**를 눌러 필터를 제거한다.
- 날짜 범위의 시작일이 종료일보다 늦지 않은지 확인한다.
- 선택한 환경, DAG, Task 또는 상태가 실제 데이터와 일치하는지 확인한다.

### 30일 그래프가 일부만 보임

그래프 아래 가로 스크롤을 왼쪽으로 이동하면 과거 날짜를 볼 수 있다. 최초 위치는 최신 날짜가 있는
우측 끝이다.

### 시간이 예상과 다름

상단 Timezone 선택값을 확인한다. 데이터 저장과 API 기준은 UTC이며 브라우저가 선택한 시간대로
변환해 표시한다.

### 로컬 네트워크에서 데모에 접속할 수 없음

- 서버를 `--host 0.0.0.0`으로 실행했는지 확인한다.
- URL에 `127.0.0.1`이 아닌 서버 장비의 실제 IP를 사용한다.
- 서버 장비와 접속 장비가 같은 네트워크에 있는지 확인한다.
- 운영체제 방화벽에서 TCP 8000번 포트가 차단되지 않았는지 확인한다.

### Managed Connection을 저장하거나 테스트할 수 없음

- Admin 계정인지 확인한다.
- 환경, Provider별 필수 항목과 URL 형식을 확인한다.
- Airflow 또는 Slack 연결 생성 시 필요한 Token을 입력했는지 확인한다.
- 운영자가 Managed Connection 암호화 키를 설정했는지 확인한다.
- 외부 Provider 주소가 DagSentry 서버에서 접근 가능한지 확인한다.

## 11. 보안상 주의사항

- 운영 비밀번호, Token, API Key를 URL, 로그, 이슈 또는 채팅에 남기지 않는다.
- 데모 계정과 비밀번호를 운영 환경에서 사용하지 않는다.
- Secret 수정 화면이 비어 있는 것은 정상이며 기존 값이 삭제되었다는 뜻이 아니다.
- `REJECTED` AI 결과를 실제 조치의 단독 근거로 사용하지 않는다.
- Incident 상태 변경 전 현재 상태와 대상 DAG/Task를 다시 확인한다.

## 12. 관련 문서

- [Web UI](web-ui.md)
- [Incident 관리](incident-management.md)
- [Error Signature](error-signature.md)
- [Diagnosis 저장과 재사용](diagnosis-persistence.md)
- [Managed Connections](managed-connections.md)
- [운영 가이드](operations.md)
- [프로덕션 배포](production-deployment.md)
